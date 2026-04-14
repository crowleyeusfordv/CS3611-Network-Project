import queue
import threading
import time
import socket

'''
模拟真实网络中的拥塞情况
'''

class VirtualLink:
    def __init__(self, target_ip: str, target_port: int):
        self.target_address = (target_ip, target_port)
        # 初始化原生的 UDP Socket [cite: 43]
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        
        # 模拟有限队列：最大缓存 20 个包 
        self.buffer = queue.Queue(maxsize=20)
        self.running = True
        # 真实丢包计数：用于让拥塞控制策略感知“链路层丢弃”
        self._dropped_packets = 0
        self._drop_lock = threading.Lock()

        # 开启一个后台消费者线程，负责匀速发包
        self.worker_thread = threading.Thread(target=self._send_loop, daemon=True)
        self.worker_thread.start()

    def _send_loop(self):
        """后台线程：模拟固定带宽的漏斗"""
        while self.running:
            if not self.buffer.empty():
                packet = self.buffer.get()
                # 从队列中取出包，真实发往目标地址
                self.sock.sendto(packet, self.target_address)
            
            # 每 10ms 漏出一个包，以此模拟 100包/秒 的带宽限制 
            time.sleep(0.01)

    def send(self, packet: bytes):
        """发送端主线程调用的发送接口"""
        try:
            # 尝试把包塞进队列。put_nowait 非阻塞，满载时抛出异常。
            # 前 20 个包进入虚拟队列，产生真实排队延迟 [cite: 19]
            self.buffer.put_nowait(packet)
        except queue.Full:
            # 队列已满（超出 20 的容量），被模拟器直接抛弃，不执行 sendto [cite: 19]
            with self._drop_lock:
                self._dropped_packets += 1
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