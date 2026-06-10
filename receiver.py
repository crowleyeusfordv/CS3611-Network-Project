import argparse
import socket
import struct
from protocol import parse_packet


def start_receiver(host='127.0.0.1', port=8888):
    # 初始化原生 UDP Socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((host, port))
    print(f"[Receiver] 正在监听 {host}:{port} ...")

    expected_seq = 1
    out_of_order = set()
    session_addr = None

    while True:
        # 接收数据
        data, addr = sock.recvfrom(2048)

        try:
            # 尝试使用我们写好的协议拆包
            seq_num, timestamp, payload = parse_packet(data)

            if session_addr is None:
                session_addr = addr
            elif seq_num == 1 and addr != session_addr:
                print("[Receiver] 检测到新的传输会话，重置累计 ACK 状态。")
                expected_seq = 1
                out_of_order.clear()
                session_addr = addr

            if seq_num == expected_seq:
                expected_seq += 1
                while expected_seq in out_of_order:
                    out_of_order.remove(expected_seq)
                    expected_seq += 1
            elif seq_num > expected_seq:
                # 先缓存乱序到达的包，等缺口补上后再推进累计 ACK。
                out_of_order.add(seq_num)

            ack_num = expected_seq - 1

            # 回复携带累计 ACK Number 的 UDP 报文
            # '!I' 代表将 seq_num 打包为 4字节的无符号整数
            ack_packet = struct.pack('!I', ack_num)
            sock.sendto(ack_packet, addr)

            # 打印日志（每累计确认 10 个包打印一次，防止终端刷屏卡顿）
            if ack_num > 0 and ack_num % 10 == 0:
                print(f"[Receiver] 累计确认到 Packet {ack_num}")

        except (struct.error, ValueError):
            print("[Receiver] 收到无法解析的异常数据包")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CS3611 UDP 可靠传输接收端")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8888)
    args = parser.parse_args()
    start_receiver(args.host, args.port)
