import queue
import threading
import time
import socket
from protocol import peek_sequence

'''
模拟真实网络中的拥塞情况
'''

class VirtualLink:
    def __init__(
        self,
        target_ip: str,
        target_port: int,
        bandwidth_pps: float = 100.0,
        queue_size: int = 20,
        drop_once_sequences=None,
    ):
        self.target_address = (target_ip, target_port)
        # 初始化原生的 UDP Socket [cite: 43]
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        # 模拟有限队列：默认最大缓存 20 个包
        self.buffer = queue.Queue(maxsize=queue_size)
        self.running = True
        self._bandwidth_pps = float(bandwidth_pps)
        self._bandwidth_lock = threading.Lock()
        self._drop_once_sequences = set(drop_once_sequences or [])
        self._already_dropped_once = set()
        self._drop_log_count = 0
        # 真实丢包计数：用于让拥塞控制策略感知“链路层丢弃”
        self._dropped_packets = 0
        self._drop_lock = threading.Lock()

        # 开启一个后台消费者线程，负责匀速发包
        self.worker_thread = threading.Thread(target=self._send_loop, daemon=True)
        self.worker_thread.start()

    def _send_loop(self):
        """后台线程：模拟固定带宽的漏斗"""
        while self.running:
            try:
                packet = self.buffer.get(timeout=0.05)
                # 从队列中取出包，真实发往目标地址
                self.sock.sendto(packet, self.target_address)
            except queue.Empty:
                continue
            except OSError:
                if not self.running:
                    break
                raise

            # 默认每 10ms 漏出一个包，以此模拟 100包/秒 的带宽限制
            time.sleep(self._current_send_interval())

    def _current_send_interval(self) -> float:
        with self._bandwidth_lock:
            bandwidth_pps = max(self._bandwidth_pps, 1e-6)
        return 1.0 / bandwidth_pps

    def set_bandwidth_pps(self, bandwidth_pps: float):
        """运行中调整瓶颈带宽，用于动态网络突变实验。"""
        if bandwidth_pps <= 0:
            raise ValueError("bandwidth_pps 必须为正数")
        with self._bandwidth_lock:
            self._bandwidth_pps = float(bandwidth_pps)

    def get_bandwidth_pps(self) -> float:
        with self._bandwidth_lock:
            return self._bandwidth_pps

    def send(self, packet: bytes):
        """发送端主线程调用的发送接口"""
        try:
            seq_num = peek_sequence(packet)
            if seq_num in self._drop_once_sequences and seq_num not in self._already_dropped_once:
                self._already_dropped_once.add(seq_num)
                self._record_drop()
                print(f"[VirtualLink] 故障注入：首次丢弃 Packet {seq_num}。")
                return
        except ValueError:
            pass

        try:
            # 尝试把包塞进队列。put_nowait 非阻塞，满载时抛出异常。
            # 前 20 个包进入虚拟队列，产生真实排队延迟 [cite: 19]
            self.buffer.put_nowait(packet)
        except queue.Full:
            # 队列已满（超出 20 的容量），被模拟器直接抛弃，不执行 sendto [cite: 19]
            self._record_drop()
            self._log_overflow_drop()

    def _record_drop(self):
        with self._drop_lock:
            self._dropped_packets += 1

    def _log_overflow_drop(self):
        self._drop_log_count += 1
        if self._drop_log_count <= 5 or self._drop_log_count % 50 == 0:
            print("[VirtualLink] 队列已满！模拟器抛弃该包，触发拥塞丢包。")

    def drain_dropped_count(self) -> int:
        """读取并清零累计丢包数量（线程安全）。"""
        with self._drop_lock:
            count = self._dropped_packets
            self._dropped_packets = 0
        return count
            
    def close(self):
        self.running = False
        self.sock.close()
        self.worker_thread.join(timeout=1.0)
