import argparse
import json
import math
import os
import socket
import struct
import threading
import time

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from protocol import PAYLOAD_SIZE, make_packet, parse_packet
from q_learning_agent import QLearningAgent
from virtual_link import VirtualLink


MODEL_VERSION = "q-learning-periodic-v3"


class ReliableSender:
    def __init__(
        self,
        target_ip,
        target_port,
        controller: str = "q",
        bandwidth_pps: float = 100.0,
        queue_size: int = 20,
        drop_once_sequences=None,
        stats_interval: float = 0.3,
        enable_fast_retransmit: bool = True,
    ):
        self.link = VirtualLink(
            target_ip,
            target_port,
            bandwidth_pps=bandwidth_pps,
            queue_size=queue_size,
            drop_once_sequences=drop_once_sequences,
        )
        self.sock = self.link.sock
        self.sock.settimeout(0.2)

        self.controller = controller
        self.stats_interval = stats_interval
        self.enable_fast_retransmit = enable_fast_retransmit
        self.lock = threading.RLock()
        self.threads = []
        self.running = True

        self.unacked_packets = {}
        self.highest_acked = 0
        self.duplicate_ack_counts = {}

        self.cwnd = 1.0
        self.srtt = 0.1
        self.rto = 0.3

        self.start_time = time.time()
        self.last_stats_time = self.start_time
        self.event_history = []
        self.time_history = []
        self.cwnd_history = []
        self.throughput_history = []
        self.avg_rtt_history = []
        self.loss_history = []
        self.timeout_history = []
        self.fast_retransmit_history = []

        self.period_acked_bytes = 0
        self.period_rtts = []
        self.period_timeout_count = 0
        self.period_fast_retransmit_count = 0

        self.total_acked_packets = 0
        self.total_timeout_count = 0
        self.total_fast_retransmit_count = 0
        self.total_link_drop_count = 0
        self.total_retransmissions = 0

        self.agent = None
        self.current_state = 0
        self.last_action = 0
        self.last_avg_rtt = 0.1
        if self.controller == "q":
            self.agent = QLearningAgent(epsilon=0.6)
            if self._should_load_q_table():
                self.agent.load_model()
            else:
                print("[Sender] 跳过加载旧版或低质量 q_table.npy，从零开始训练。")

    def _should_load_q_table(self) -> bool:
        stats_path = "q_table_best_stats.json"
        if not os.path.exists("q_table.npy") or not os.path.exists(stats_path):
            return False
        try:
            with open(stats_path, "r", encoding="utf-8") as f:
                stats = json.load(f)
            return stats.get("model_version") == MODEL_VERSION and stats.get("best_max_cwnd", 0) >= 5
        except (OSError, ValueError, TypeError):
            return False

    def start_background_tasks(self):
        self._start_thread(self._receive_acks_thread)
        self._start_thread(self._timeout_retransmit_thread)
        self._start_thread(self._control_and_stats_thread)

    def _start_thread(self, target):
        thread = threading.Thread(target=target, daemon=True)
        self.threads.append(thread)
        thread.start()

    def _receive_acks_thread(self):
        while self.running:
            try:
                data, _ = self.sock.recvfrom(1024)
                if len(data) != 4:
                    continue
                ack_num, = struct.unpack("!I", data)
                self._handle_ack(ack_num)
            except socket.timeout:
                continue
            except OSError:
                if not self.running:
                    break
                raise
            except Exception as exc:
                print(f"[Sender] 收到无法解析的 ACK: {exc}")

    def _handle_ack(self, ack_num: int):
        now = time.time()
        with self.lock:
            if ack_num > self.highest_acked:
                newly_acked = [seq for seq in self.unacked_packets if seq <= ack_num]
                for seq in sorted(newly_acked):
                    entry = self.unacked_packets.pop(seq)
                    self.period_acked_bytes += PAYLOAD_SIZE
                    self.total_acked_packets += 1

                    if not entry["retransmitted"]:
                        current_rtt = now - entry["timestamp"]
                        self._update_rtt_locked(current_rtt)
                        self.period_rtts.append(current_rtt)

                    if self.controller == "aimd":
                        self.cwnd += 1.0 / max(self.cwnd, 1e-9)

                self.highest_acked = ack_num
                self.duplicate_ack_counts.clear()
                return

            if ack_num == self.highest_acked:
                count = self.duplicate_ack_counts.get(ack_num, 0) + 1
                self.duplicate_ack_counts[ack_num] = count
                if count == 3 and self.enable_fast_retransmit:
                    missing_seq = ack_num + 1
                    if missing_seq in self.unacked_packets:
                        self._fast_retransmit_locked(missing_seq, ack_num)

    def _update_rtt_locked(self, current_rtt: float):
        self.srtt = 0.875 * self.srtt + 0.125 * current_rtt
        self.rto = max(0.1, self.srtt * 1.5)

    def _fast_retransmit_locked(self, seq: int, duplicate_ack: int):
        entry = self.unacked_packets[seq]
        print(f"[Sender-FastRetransmit] 连续 3 个重复 ACK {duplicate_ack}，立即重传 Packet {seq}。")
        self.link.send(entry["packet"])
        entry["last_sent"] = time.time()
        entry["retransmitted"] = True
        self.unacked_packets[seq] = entry

        self.period_fast_retransmit_count += 1
        self.total_fast_retransmit_count += 1
        self.total_retransmissions += 1

        if self.controller == "aimd":
            self.cwnd = max(1.0, self.cwnd / 2.0)

    def _timeout_retransmit_thread(self):
        while self.running:
            time.sleep(0.05)
            with self.lock:
                now = time.time()
                for seq, entry in list(self.unacked_packets.items()):
                    if now - entry["last_sent"] > self.rto:
                        print(f"[Sender-RTO] Packet {seq} 超时 ({self.rto:.3f}s)，触发重传。")
                        self.link.send(entry["packet"])
                        entry["last_sent"] = now
                        entry["retransmitted"] = True
                        self.unacked_packets[seq] = entry

                        self.period_timeout_count += 1
                        self.total_timeout_count += 1
                        self.total_retransmissions += 1

                        if self.controller == "aimd":
                            self.cwnd = max(1.0, self.cwnd / 2.0)

    def _control_and_stats_thread(self):
        while self.running:
            time.sleep(max(self.stats_interval, self.srtt))
            self.record_period()

    def record_period(self):
        with self.lock:
            now = time.time()
            elapsed = now - self.start_time
            period_duration = max(now - self.last_stats_time, 1e-9)
            self.last_stats_time = now

            throughput_mbps = (self.period_acked_bytes * 8) / (period_duration * 1_000_000)
            avg_rtt = sum(self.period_rtts) / len(self.period_rtts) if self.period_rtts else self.srtt
            link_drops = self.link.drain_dropped_count()
            self.total_link_drop_count += link_drops
            loss_count = link_drops + self.period_timeout_count + self.period_fast_retransmit_count

            if avg_rtt > self.last_avg_rtt * 1.25:
                rtt_trend = 2
            elif avg_rtt < self.last_avg_rtt * 0.8:
                rtt_trend = 0
            else:
                rtt_trend = 1

            if self.controller == "q" and self.agent is not None:
                loss_event = 1 if loss_count > 0 else 0
                next_state = self.agent.get_state_index(rtt_trend, loss_event)
                reward = self.agent.calculate_reward(throughput_mbps, avg_rtt, loss_count)
                self.agent.learn(self.current_state, self.last_action, reward, next_state)
                action = self.agent.choose_action(next_state)
                self.last_action = action
                self.current_state = next_state

                if action == 1:
                    self.cwnd += 1.0
                elif action == 2:
                    self.cwnd = max(1.0, self.cwnd / 2.0)

                self.agent.decay_epsilon(min_epsilon=0.08, decay_rate=0.995)

            self.time_history.append(elapsed)
            self.cwnd_history.append(self.cwnd)
            self.throughput_history.append(throughput_mbps)
            self.avg_rtt_history.append(avg_rtt)
            self.loss_history.append(loss_count)
            self.timeout_history.append(self.period_timeout_count)
            self.fast_retransmit_history.append(self.period_fast_retransmit_count)

            self.period_acked_bytes = 0
            self.period_rtts.clear()
            self.period_timeout_count = 0
            self.period_fast_retransmit_count = 0
            self.last_avg_rtt = 0.85 * self.last_avg_rtt + 0.15 * avg_rtt

    def schedule_bandwidth_drop(self, after_seconds: float, new_bandwidth_pps: float):
        def worker():
            time.sleep(after_seconds)
            if not self.running:
                return
            self.link.set_bandwidth_pps(new_bandwidth_pps)
            with self.lock:
                elapsed = time.time() - self.start_time
                self.event_history.append((elapsed, f"bandwidth={new_bandwidth_pps:g}pps"))
            print(f"[Sender] 动态突变：带宽调整为 {new_bandwidth_pps:g} packets/s。")

        self._start_thread(worker)

    def send_data(self, total_packets: int, max_runtime=None):
        print(f"[Sender-{self.controller.upper()}] 准备发送 {total_packets} 个数据包...")
        seq = 1
        deadline = time.time() + max_runtime if max_runtime else None

        while seq <= total_packets or len(self.unacked_packets) > 0:
            if deadline and time.time() > deadline:
                raise TimeoutError(f"发送超过 {max_runtime} 秒仍未完成")

            with self.lock:
                effective_cwnd = max(1, math.ceil(self.cwnd))
                can_send = seq <= total_packets and len(self.unacked_packets) < effective_cwnd

            if can_send:
                payload = f"Message payload for packet {seq}".encode("utf-8")
                packet_bytes = make_packet(seq, payload)
                _, timestamp, _ = parse_packet(packet_bytes)

                with self.lock:
                    self.unacked_packets[seq] = {
                        "packet": packet_bytes,
                        "timestamp": timestamp,
                        "last_sent": time.time(),
                        "retransmitted": False,
                    }

                self.link.send(packet_bytes)
                seq += 1
            else:
                time.sleep(0.001)

        self.record_period()
        print(f"[Sender-{self.controller.upper()}] 所有数据包发送并确认完毕！")

    def save_agent_if_useful(self):
        if self.controller != "q" or self.agent is None:
            return

        max_cwnd = max(self.cwnd_history) if self.cwnd_history else self.cwnd
        stats_path = "q_table_best_stats.json"
        best_max_cwnd = 0.0
        saved_version = None
        try:
            if os.path.exists(stats_path):
                with open(stats_path, "r", encoding="utf-8") as f:
                    stats = json.load(f)
                best_max_cwnd = float(stats.get("best_max_cwnd", 0.0))
                saved_version = stats.get("model_version")
        except (OSError, ValueError, TypeError):
            pass

        if saved_version != MODEL_VERSION or max_cwnd >= best_max_cwnd:
            self.agent.save_model()
            with open(stats_path, "w", encoding="utf-8") as f:
                json.dump({"best_max_cwnd": max_cwnd, "model_version": MODEL_VERSION}, f, indent=2)
            print(f"[Sender] 已保存 Q-Table (max_cwnd={max_cwnd:.2f})。")
        else:
            print(f"[Sender] 本次 max_cwnd={max_cwnd:.2f} 未超过历史最优 {best_max_cwnd:.2f}，跳过保存。")

    def summary(self):
        return {
            "controller": self.controller,
            "avg_throughput_mbps": average(self.throughput_history),
            "avg_rtt": average(self.avg_rtt_history),
            "max_cwnd": max(self.cwnd_history) if self.cwnd_history else self.cwnd,
            "total_acked_packets": self.total_acked_packets,
            "total_link_drops": self.total_link_drop_count,
            "total_timeouts": self.total_timeout_count,
            "total_fast_retransmits": self.total_fast_retransmit_count,
            "total_retransmissions": self.total_retransmissions,
        }

    def close(self):
        self.running = False
        self.link.close()
        for thread in self.threads:
            thread.join(timeout=1.0)


def average(values):
    return float(sum(values) / len(values)) if values else 0.0


def parse_drop_once(value: str) -> list[int]:
    if not value:
        return []
    return [int(part.strip()) for part in value.split(",") if part.strip()]


def run_controller(controller: str, args) -> ReliableSender:
    sender = ReliableSender(
        args.host,
        args.port,
        controller=controller,
        bandwidth_pps=args.bandwidth_pps,
        queue_size=args.queue_size,
        drop_once_sequences=parse_drop_once(args.drop_once),
        stats_interval=args.stats_interval,
        enable_fast_retransmit=not args.disable_fast_retransmit,
    )
    sender.start_background_tasks()
    if args.dynamic:
        sender.schedule_bandwidth_drop(args.dynamic_after, args.dynamic_bandwidth_pps)
    try:
        sender.send_data(args.packets, max_runtime=args.max_runtime)
        time.sleep(args.cooldown)
        if args.cooldown > 0:
            sender.record_period()
        if not args.no_save_model:
            sender.save_agent_if_useful()
        return sender
    except Exception:
        sender.close()
        raise


def plot_single_cwnd(sender: ReliableSender, output_dir: str):
    filenames = {"aimd": "aimd_result.png", "q": "q_learning_result.png"}
    filename = filenames.get(sender.controller, f"{sender.controller}_result.png")
    plt.figure(figsize=(10, 5))
    plt.plot(sender.time_history, sender.cwnd_history, label=f"{sender.controller.upper()} CWND", linewidth=1.5)
    add_event_lines(sender)
    plt.xlabel("Time (seconds)")
    plt.ylabel("CWND Size (Packets)")
    plt.title(f"{sender.controller.upper()} Congestion Window Dynamics")
    plt.grid(True)
    plt.legend()
    plt.tight_layout()
    plt.savefig(os.path.join(output_dir, filename))
    plt.close()


def plot_cwnd_comparison(results: list[ReliableSender], output_dir: str):
    plt.figure(figsize=(10, 5))
    colors = {"aimd": "blue", "q": "green"}
    for sender in results:
        plt.plot(
            sender.time_history,
            sender.cwnd_history,
            label=f"{sender.controller.upper()} CWND",
            linewidth=1.5,
            color=colors.get(sender.controller),
        )
    if results:
        add_event_lines(results[0])
    plt.xlabel("Time (seconds)")
    plt.ylabel("CWND Size (Packets)")
    plt.title("Congestion Window Comparison (AIMD vs Q-Learning)")
    plt.grid(True)
    plt.legend()
    plt.tight_layout()
    plt.savefig(os.path.join(output_dir, "cwnd_compare.png"))
    plt.close()


def plot_throughput_delay(results: list[ReliableSender], output_dir: str):
    labels = [sender.controller.upper() for sender in results]
    throughput = [average(sender.throughput_history) for sender in results]
    rtt = [average(sender.avg_rtt_history) for sender in results]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 5))
    bars1 = ax1.bar(labels, throughput, color=["blue" if label == "AIMD" else "green" for label in labels])
    ax1.set_title("Average Throughput")
    ax1.set_ylabel("Mbps")
    ax1.grid(True, axis="y", linestyle="--", alpha=0.4)

    bars2 = ax2.bar(labels, rtt, color=["blue" if label == "AIMD" else "green" for label in labels])
    ax2.set_title("Average RTT")
    ax2.set_ylabel("Seconds")
    ax2.grid(True, axis="y", linestyle="--", alpha=0.4)

    for bars, ax in ((bars1, ax1), (bars2, ax2)):
        for bar in bars:
            height = bar.get_height()
            ax.text(bar.get_x() + bar.get_width() / 2, height, f"{height:.3f}", ha="center", va="bottom", fontsize=9)

    plt.tight_layout()
    plt.savefig(os.path.join(output_dir, "throughput_delay_bar.png"))
    plt.close()


def add_event_lines(sender: ReliableSender):
    for event_time, label in sender.event_history:
        plt.axvline(event_time, color="red", linestyle="--", linewidth=1.0, alpha=0.75)
        ymax = plt.ylim()[1]
        plt.text(event_time, ymax * 0.95, label, color="red", rotation=90, va="top", ha="right", fontsize=8)


def save_summary(results: list[ReliableSender], output_dir: str):
    summary = [sender.summary() for sender in results]
    with open(os.path.join(output_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    for item in summary:
        print(
            "[Summary] {controller}: avg_throughput={avg_throughput_mbps:.3f}Mbps, "
            "avg_rtt={avg_rtt:.3f}s, max_cwnd={max_cwnd:.2f}, "
            "timeouts={total_timeouts}, fast_retx={total_fast_retransmits}, drops={total_link_drops}".format(**item)
        )


def build_arg_parser():
    parser = argparse.ArgumentParser(description="CS3611 UDP 可靠传输发送端与实验入口")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8888)
    parser.add_argument("--controller", choices=["aimd", "q", "both"], default="both")
    parser.add_argument("--packets", type=int, default=2000)
    parser.add_argument("--bandwidth-pps", type=float, default=100.0)
    parser.add_argument("--queue-size", type=int, default=20)
    parser.add_argument("--stats-interval", type=float, default=0.3)
    parser.add_argument("--dynamic", action="store_true")
    parser.add_argument("--dynamic-after", type=float, default=8.0)
    parser.add_argument("--dynamic-bandwidth-pps", type=float, default=50.0)
    parser.add_argument("--drop-once", default="", help="逗号分隔的序号列表，例如 15 或 15,30")
    parser.add_argument("--disable-fast-retransmit", action="store_true")
    parser.add_argument("--output-dir", default=".")
    parser.add_argument("--cooldown", type=float, default=0.0)
    parser.add_argument("--max-runtime", type=float, default=None)
    parser.add_argument("--no-save-model", action="store_true")
    parser.add_argument("--show", action="store_true")
    return parser


def main():
    args = build_arg_parser().parse_args()
    os.makedirs(args.output_dir, exist_ok=True)
    controllers = ["aimd", "q"] if args.controller == "both" else [args.controller]
    results = []
    try:
        for controller in controllers:
            result = run_controller(controller, args)
            results.append(result)
            result.close()

        for sender in results:
            plot_single_cwnd(sender, args.output_dir)
        plot_cwnd_comparison(results, args.output_dir)
        plot_throughput_delay(results, args.output_dir)
        save_summary(results, args.output_dir)

        if args.show:
            print("[Sender] 图表已保存；当前使用 Agg 后端，--show 仅保留为兼容参数。")
    finally:
        for sender in results:
            sender.close()


if __name__ == "__main__":
    main()
