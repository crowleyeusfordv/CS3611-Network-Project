import struct
import time
'''
处理数据在网络传输前的“打包”和接收后的“拆包”
'''
def make_packet(seq_num: int, payload_data: bytes) -> bytes:
    """
    将序列号、时间戳和数据打包成二进制流。
    """
    timestamp = time.time()  # 获取当前高精度时间戳
    
    # 确保 payload 刚好是 1024 字节，不足的用空字符 b'\0' 补齐 
    padded_payload = payload_data.ljust(1024, b'\0')
    
    # '!I d 1024s' 是打包规则：
    # ! 代表网络字节序 (大端，保证跨平台一致性)
    # I 代表无符号整数 (4字节，用于 Sequence Number) 
    # d 代表双精度浮点数 (8字节，用于 Timestamp) 
    # 1024s 代表 1024 字节的字符流 (用于 Payload) 
    return struct.pack('!I d 1024s', seq_num, timestamp, padded_payload)

def parse_packet(packet: bytes):
    """
    将接收到的二进制流拆包还原。
    """
    seq_num, timestamp, payload = struct.unpack('!I d 1024s', packet)
    # 去除 payload 尾部为了对齐而填充的空字符
    payload = payload.rstrip(b'\0')
    return seq_num, timestamp, payload