import socket
import threading
import time
import struct
import math
import json
import os
from virtual_link import VirtualLink
from protocol import make_packet, parse_packet
import matplotlib.pyplot as plt
from q_learning_agent import QLearningAgent
class ReliableSender:
    def __init__(self, target_ip, target_port, controller: str = "q"):
        """
        controller:
          - "q": Q-learning 拥塞控制
          - "aimd": TCP Reno 风格基线（每次 ACK 加性增；超时乘性减）
        """
        # 1. 虚拟瓶颈链路封装：在 sendto() 外层构建模拟器 
        self.link = VirtualLink(target_ip, target_port)
        # 复用模拟器的底层 socket 来接收 ACK
        self.sock = self.link.sock  
        self.sock.settimeout(1.0) # 设置非阻塞超时，方便线程安全退出
        
        # 2. 可靠传输核心数据结构
        # 未确认队列：
        # {seq_num: {"packet": bytes, "timestamp": float, "last_sent": float, "retransmitted": bool}}
        self.unacked_packets = {}  
        # 线程锁：因为发包、收ACK、超时检测这三个线程都会同时读写上述字典
        self.lock = threading.Lock() 
        
        # 3. 拥塞控制与网络状态变量 (这些是你要暴露给 AI 队友的接口)
        self.cwnd = 1.0  # 初始化拥塞窗口大小 [cite: 22]
        self.srtt = 0.1  # 平滑 RTT，初始暂定 100ms 
        self.rto = 0.3   # 重传超时时间，初始暂定 300ms 

        self.controller = controller
        
        # --- 新增：用于后续 matplotlib 绘图的数据记录 ---
        self.start_time = time.time()
        self.time_history = []
        self.cwnd_history = []
        self.throughput_history = []
        self.avg_rtt_history = []
        self.loss_history = []

        self.running = True

        # --- Q-Learning 相关初始化（仅在 controller="q" 时启用）---
        self.agent = None
        self.current_state = 0
        self.last_action = 0
        if self.controller == "q":
            # 初始探索率提高，避免从“保守 Q 表”开始时长期卡在动作 2（减半）导致退化。
            self.agent = QLearningAgent(epsilon=0.8)

            # 如果历史最佳 CWND 很低，说明当前磁盘里的 q_table.npy 很可能已经退化。
            # 这种情况下跳过 load_model()，让本次从零重新探索。
            stats_path = 'q_table_best_stats.json'
            best_max_cwnd = 0.0
            try:
                if os.path.exists(stats_path):
                    with open(stats_path, 'r', encoding='utf-8') as f:
                        best_max_cwnd = float(json.load(f).get('best_max_cwnd', 0.0))
            except Exception:
                best_max_cwnd = 0.0

            if best_max_cwnd >= 10.0:
                self.agent.load_model()  # 尝试加载预训练 Q-Table
            else:
                print(f"[Sender] 跳过加载 q_table.npy（best_max_cwnd={best_max_cwnd:.2f} < 10）。")
        
        # 周期统计变量 (用于喂给 AI 计算奖励和状态)
        self.period_throughput = 0
        self.period_rtts = []
        # 注意：这里的“丢包”由 VirtualLink 的队列溢出统计提供
        self.period_timeout_count = 0
        self.last_avg_rtt = 0.1 # 用于判断 RTT 趋势
        # 连续丢包计数：用来避免偶发少量丢包导致策略瞬间过度恐慌
        self.loss_streak = 0

    def _receive_acks_thread(self):
        """后台线程 1：专门负责接收 Receiver 发回的 ACK 并计算 RTT"""
        while self.running:
            try:
                # 接收 ACK 包（Receiver 回复的是 4 字节的序号）
                data, _ = self.sock.recvfrom(1024)
                if len(data) == 4:
                    ack_num, = struct.unpack('!I', data)
                    
                    with self.lock:
                        if ack_num in self.unacked_packets:
                            entry = self.unacked_packets.pop(ack_num)
                            timestamp = entry["timestamp"]
                            was_retransmitted = entry["retransmitted"]

                            # 1. 采样当前 RTT（使用报文头里的 Timestamp）
                            current_rtt = time.time() - timestamp

                            # 2. Karn 思路：重传后的段不参与 RTT 估计，避免重传导致的“假低 RTT”收缩 RTO
                            if not was_retransmitted:
                                # 3. 平滑计算 SRTT (经典的指数加权移动平均算法)
                                self.srtt = 0.875 * self.srtt + 0.125 * current_rtt
                                # 4. 动态更新重传超时时间 RTO（简单估算为 SRTT 的 1.5 倍）
                                self.rto = max(0.1, self.srtt * 1.5)

                            # AIMD 拥塞避免：加性增 (Additive Increase)
                            # 每收到一个ACK: CWND += 1/CWND [cite: 23]
                            # 队友可以在这里更新拥塞避免阶段的 CWND (如 CWND += 1/CWND)
                            # 或者把 current_rtt 喂给 Q-Table 当作状态输入
                            self.period_throughput += 1
                            # 只有“非重传确认”的 RTT 样本才用于状态评估
                            if not was_retransmitted:
                                self.period_rtts.append(current_rtt)

                            # 拥塞控制更新（只在“首次确认该包”时执行）
                            if self.controller == "aimd":
                                # AIMD：每收到一个 ACK，加性增：cwnd += 1/cwnd
                                self.cwnd += 1.0 / max(self.cwnd, 1e-9)
                            else:
                                # Q-learning：仅在 action==1 时对每个 ACK 做加性增
                                if self.last_action == 1:
                                    self.cwnd += 1.0 / max(self.cwnd, 1e-9)
                            
            except socket.timeout:
                # 因为在 __init__ 中设置了 sock.settimeout(1.0)
                # 没收到数据会触发超时，这使得 while 循环能定期检查 self.running 标志位，安全退出
                continue
            except OSError as e:
                # close() 后 socket 可能触发 EBADF；如果正在停止则直接退出线程
                if not self.running:
                    break
                print(f"[Sender] socket OSError: {e}")
            except Exception as e:
                print(f"[Sender] 收到无法解析的异常数据包: {e}")
    def _timeout_retransmit_thread(self):
        """后台线程 2：定时扫描，判定丢包并触发重传"""
        while self.running:
            time.sleep(0.05)  # 每 50ms 扫描一次，避免过度占用 CPU
            
            with self.lock:
                current_time = time.time()
                # 遍历所有未确认的包
                # 注意：使用 list() 是为了在遍历字典时允许修改字典内容
                for seq, entry in list(self.unacked_packets.items()):
                    # 如果当前时间距离发送时间超过了 RTO（重传超时时间）
                    if current_time - entry["last_sent"] > self.rto:
                        print(f"[Sender-RTO] 包 {seq} 超时 ({self.rto:.3f}s)！触发重传...")
                        
                        # 1. 重新发送该包
                        packet_bytes = entry["packet"]
                        self.link.send(packet_bytes)
                        
                        # 2. 更新该包的发送时间，重新开始计时
                        entry["last_sent"] = current_time
                        entry["retransmitted"] = True
                        self.unacked_packets[seq] = entry
                        self.period_timeout_count += 1
                        
                        # AIMD：超时重传触发乘性减半
                        if self.controller == "aimd":
                            self.cwnd = max(1.0, self.cwnd / 2.0)
                        # ==========================================
                        # [交接给 AI 队友的关键接口]
                        # 队友的 AIMD 算法或 Q-Learning 算法都在这里获取“丢包信号”！
                        # 
                        # 1. 对于 AIMD 对照组：一旦发生超时丢包，队友需要在这里加入乘性减逻辑：
                        #    self.cwnd = max(1.0, self.cwnd / 2.0) [cite: 24]
                        # 2. 对于 Q-Learning：队友需要在这里记录状态空间中的“发生丢包事件” [cite: 28]，
                        #    并在奖励函数 (Reward) 中增加丢包数量的负反馈惩罚 [cite: 30]。
                        # ==========================================
    def start_background_tasks(self):
        """启动所有后台监控线程"""
        threading.Thread(target=self._receive_acks_thread, daemon=True).start()
        threading.Thread(target=self._timeout_retransmit_thread, daemon=True).start()
        if self.controller == "q":
            threading.Thread(target=self._ai_controller_thread, daemon=True).start() # <--- 新增
    def send_data(self, total_packets):
        """主线程应用层调用：根据 CWND 控制发包节奏"""
        print(f"[Sender] 准备发送 {total_packets} 个数据包...")
        seq = 1
        
        # 只要还有包没发完，或者还有包没被确认，循环就不结束
        while seq <= total_packets or len(self.unacked_packets) > 0:
            
            # 1. 检查当前未确认队列的长度是否小于拥塞窗口 (CWND)
            with self.lock:
                # 使用 ceil 避免 CWND 半折到 1.x 时被 int() 截断成 1
                # 从而让“动作2下一瞬间必跌回1”的假象更少出现。
                effective_cwnd = max(1, math.ceil(self.cwnd))
                can_send = len(self.unacked_packets) < effective_cwnd
                
            # 2. 如果窗口有空余，且还有数据要发
            if can_send and seq <= total_packets:
                payload = f"Message payload for packet {seq}".encode('utf-8')
                packet_bytes = make_packet(seq, payload)
                _, timestamp, _ = parse_packet(packet_bytes)
                
                # 记录发送时间并加入“未确认队列”
                with self.lock:
                    self.unacked_packets[seq] = {
                        "packet": packet_bytes,
                        "timestamp": timestamp,
                        "last_sent": time.time(),
                        "retransmitted": False,
                    }
                
                # 交给虚拟链路发往真实的网卡
                self.link.send(packet_bytes)
                seq += 1
            else:
                # 窗口满了（触发了拥塞控制），稍微让出 CPU，等待 ACK 到来腾出窗口空间
                time.sleep(0.001)
                
        print("[Sender] 所有数据包发送并确认完毕！")        
        
    def close(self):
        self.running = False
        self.link.close()

    def _ai_controller_thread(self):
        """后台线程 3：AI 拥塞控制大脑，按 SRTT 周期执行"""
        while self.running:
            # 动态等待一个 SRTT 周期 (限制最小等待时间)
            # 再略微增大最小周期，减少单个控制周期内样本抖动带来的过冲。
            # 注意：这个周期需要和 ACK/RTT 的到达节奏对齐，否则 throughput 统计会偏小，
            # agent 就学不到“更大 cwnd 让吞吐变高”的真实回报。
            time.sleep(max(0.3, self.srtt))
            
            with self.lock:
                # 1. 结算本周期的统计数据
                throughput = self.period_throughput
                # 2. 统计虚拟链路的“真实丢包”（队列溢出）
                #    这比用 RTO 超时来近似丢包更贴近题目里的链路层丢失。
                loss_count = self.link.drain_dropped_count()
                avg_rtt = sum(self.period_rtts) / len(self.period_rtts) if self.period_rtts else self.srtt
                
                # 2. 判断 RTT 趋势 (0:变小, 1:平稳, 2:变大)
                #    适当放宽阈值 + 平滑 last_avg_rtt，减少 RTT 的小幅抖动导致的状态频繁切换。
                if avg_rtt > self.last_avg_rtt * 1.3:
                    rtt_trend = 2
                elif avg_rtt < self.last_avg_rtt * 0.7:
                    rtt_trend = 0
                else:
                    rtt_trend = 1
                    
                # 3. 判断丢包事件 (0:未丢包, 1:发生丢包)
                #    为了避免“偶发少量丢失”导致策略瞬间过度恐慌，
                #    这里不仅对 loss_count 设置阈值，还要求“连续两个周期”都出现较多丢包。
                # 放宽丢包触发，避免 loss 状态太难触发导致策略长期保守
                # 放宽丢包触发：只有当本周期丢包明显增多时才认为进入“丢包事件”
                loss_flag = 1 if loss_count >= 4 else 0
                if loss_flag == 1:
                    self.loss_streak += 1
                else:
                    self.loss_streak = 0
                # 需要更长的连续丢包窗口才触发“恐慌减半”，给 CWND 更多爬升机会。
                loss_event = 1 if self.loss_streak >= 3 else 0
                
                # 4. 状态转移与计算奖励
                next_state = self.agent.get_state_index(rtt_trend, loss_event)
                reward = self.agent.calculate_reward(throughput, avg_rtt, loss_count)
                
                # 5. AI 学习 (更新 Q-Table 矩阵)
                self.agent.learn(self.current_state, self.last_action, reward, next_state)
                
                # 6. AI 决策下一步行为
                action = self.agent.choose_action(next_state)
                self.last_action = action
                self.current_state = next_state
                
                # 7. 执行动作，动态调整 CWND
                if action == 0:
                    # 动作 0: 由 ACK 到达时的弱加性增（0.5/cwnd）来体现
                    # 控制线程本周期不额外改 cwnd。
                    pass
                elif action == 1:
                    # 动作 1 不再在控制线程里“离散 +1”，而是通过 last_action==1
                    # 在每个 ACK 上做 AIMD 加性增（见 _receive_acks_thread）。
                    pass
                elif action == 2:
                    self.cwnd = max(1.0, self.cwnd / 2.0) # 动作 2: 规避拥塞，CWND 减半
                
                # 记录画图数据
                self.time_history.append(time.time() - self.start_time)
                self.cwnd_history.append(self.cwnd)
                self.throughput_history.append(throughput)
                self.avg_rtt_history.append(avg_rtt)
                self.loss_history.append(loss_count)
                
                # 8. 清零周期数据，为下一轮做准备
                self.period_throughput = 0
                self.period_rtts.clear()
                self.period_timeout_count = 0
                # 对 last_avg_rtt 做 EMA 平滑，避免单个周期的尖峰把趋势判断带偏
                self.last_avg_rtt = 0.85 * self.last_avg_rtt + 0.15 * avg_rtt

                # 避免策略过早完全收敛到“只剩动作2”的保守策略，
                # 让它在更长时间里仍有一定探索概率。
                # 提高探索下限，避免 Q-table 偏向“动作2保守”后难以恢复到更高 CWND 探测。
                # 保持较高探索，避免再次快速收敛到“动作2保守”
                self.agent.decay_epsilon(min_epsilon=0.25, decay_rate=0.9995)

if __name__ == "__main__":
    target_host = '127.0.0.1'
    target_port = 8888

    # 增加发包量，给 AI 足够的交互轮次来学习
    total_to_send = 2000

    # 1) 跑 AIMD 基线（对照组）
    print("[Sender] 开始运行 AIMD baseline...")
    sender_aimd = ReliableSender(target_host, target_port, controller="aimd")
    sender_aimd.start_background_tasks()
    sender_aimd.send_data(total_to_send)
    time.sleep(2)
    sender_aimd.close()

    # 2) 跑 Q-learning
    print("[Sender] 开始运行 Q-Learning...")
    sender_q = ReliableSender(target_host, target_port, controller="q")
    sender_q.start_background_tasks()
    sender_q.send_data(total_to_send)
    time.sleep(2)

    # 避免“退化运行”覆盖掉历史最优策略：只有当本次 max(CWND) > 历史 best 才更新 q_table.npy。
    max_cwnd = max(sender_q.cwnd_history) if sender_q.cwnd_history else sender_q.cwnd
    stats_path = 'q_table_best_stats.json'
    best_max_cwnd = 0.0
    try:
        if os.path.exists(stats_path):
            with open(stats_path, 'r', encoding='utf-8') as f:
                best_max_cwnd = float(json.load(f).get('best_max_cwnd', 0.0))
    except Exception:
        best_max_cwnd = 0.0

    if sender_q.agent is not None and max_cwnd > best_max_cwnd:
        sender_q.agent.save_model()
        try:
            with open(stats_path, 'w', encoding='utf-8') as f:
                json.dump({'best_max_cwnd': max_cwnd}, f)
        except Exception:
            pass
        print(f"[Sender] 本次 CWND 创新纪录(max_cwnd={max_cwnd:.2f} > best={best_max_cwnd:.2f})，已保存 q_table.npy。")
    else:
        print(f"[Sender] 本次 CWND 未超过历史最优(max_cwnd={max_cwnd:.2f} <= best={best_max_cwnd:.2f})，跳过保存 q_table.npy。")
    sender_q.close()

    # ====== 绘制 AIMD vs Q-learning 对照曲线 ======
    print("[Sender] 绘制 AIMD 与 Q-Learning 对比 CWND...")
    plt.figure(figsize=(10, 5))
    plt.plot(sender_aimd.time_history, sender_aimd.cwnd_history, label='AIMD CWND', color='blue', linewidth=1.2)
    plt.plot(sender_q.time_history, sender_q.cwnd_history, label='Q-Learning CWND', color='green', linewidth=1.5)
    plt.xlabel('Time (seconds)')
    plt.ylabel('CWND Size (Packets)')
    plt.title('Congestion Window Comparison (AIMD vs Q-Learning)')
    plt.grid(True)
    plt.legend()
    plt.savefig('cwnd_compare.png')
    plt.show()

    # 仍保留 Q-learning 单曲线（便于你写文档引用）
    plt.figure(figsize=(10, 5))
    plt.plot(sender_q.time_history, sender_q.cwnd_history, label='Q-Learning CWND', color='green', linewidth=1.5)
    plt.xlabel('Time (seconds)')
    plt.ylabel('CWND Size (Packets)')
    plt.title('Q-Learning Congestion Window Dynamics')
    plt.grid(True)
    plt.legend()
    plt.savefig('q_learning_result.png')
    plt.show()

    # ====== 绘制吞吐量与延迟柱状图（基于 Q-learning 结果）======
    if sender_q.throughput_history and sender_q.avg_rtt_history:
        avg_throughput = float(sum(sender_q.throughput_history) / len(sender_q.throughput_history))
        avg_rtt = float(sum(sender_q.avg_rtt_history) / len(sender_q.avg_rtt_history))

        # 由于吞吐与 RTT 数值量级差很多，使用双 y 轴确保两根柱都可读。
        fig, ax1 = plt.subplots(figsize=(10, 5))
        x = [0, 1]
        labels = ['Throughput(ACKs/period)', 'Avg RTT(s)']

        bars1 = ax1.bar(x[0], avg_throughput, width=0.55, color='blue')
        ax1.set_ylabel('Throughput(ACKs/period)', color='blue')
        ax1.tick_params(axis='y', labelcolor='blue')
        ax1.set_title('Throughput and Delay (Q-Learning)')
        ax1.set_xticks(x, labels)
        ax1.grid(True, axis='y', linestyle='--', alpha=0.4)

        ax2 = ax1.twinx()
        bars2 = ax2.bar(x[1], avg_rtt, width=0.55, color='red')
        ax2.set_ylabel('Avg RTT(s)', color='red')
        ax2.tick_params(axis='y', labelcolor='red')

        # 在柱顶标注数值，便于报告直接引用
        for b in bars1:
            h = b.get_height()
            ax1.text(b.get_x() + b.get_width() / 2, h, f'{h:.3f}', ha='center', va='bottom', fontsize=9, color='blue')
        for b in bars2:
            h = b.get_height()
            ax2.text(b.get_x() + b.get_width() / 2, h, f'{h:.3f}', ha='center', va='bottom', fontsize=9, color='red')

        plt.tight_layout()
        plt.savefig('throughput_delay_bar.png')
        plt.show()