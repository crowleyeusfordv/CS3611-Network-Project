import socket
import struct
from protocol import parse_packet

def start_receiver(host='127.0.0.1', port=8888):
    # 初始化原生 UDP Socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((host, port))
    print(f"[Receiver] 正在监听 {host}:{port} ...")
    
    while True:
        # 接收数据
        data, addr = sock.recvfrom(2048)
        
        try:
            # 尝试使用我们写好的协议拆包
            seq_num, timestamp, payload = parse_packet(data)
            
            # 收到包后立刻回复携带 ACK Number 的 UDP 报文 
            # '!I' 代表将 seq_num 打包为 4字节的无符号整数
            ack_packet = struct.pack('!I', seq_num)
            sock.sendto(ack_packet, addr)
            
            # 打印日志（每收到 10 个包打印一次，防止终端刷屏卡顿）
            if seq_num % 10 == 0:
                print(f"[Receiver] 成功接收并确认 Packet {seq_num}")
                
        except struct.error:
            print("[Receiver] 收到无法解析的异常数据包")

if __name__ == "__main__":
    start_receiver()