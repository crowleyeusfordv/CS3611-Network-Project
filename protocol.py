import struct
import time

PAYLOAD_SIZE = 1024
PACKET_FORMAT = f"!Id{PAYLOAD_SIZE}s"
PACKET_STRUCT = struct.Struct(PACKET_FORMAT)
PACKET_SIZE = PACKET_STRUCT.size


def make_packet(seq_num: int, payload_data: bytes) -> bytes:
    """
    将序列号、时间戳和数据打包成二进制流。
    """
    if len(payload_data) > PAYLOAD_SIZE:
        raise ValueError(f"payload 不能超过 {PAYLOAD_SIZE} 字节")

    timestamp = time.time()  # 获取当前高精度时间戳

    # 确保 payload 刚好是 1024 字节，不足的用空字符 b'\0' 补齐
    padded_payload = payload_data.ljust(PAYLOAD_SIZE, b'\0')

    # '!Id1024s' 是打包规则：
    # ! 代表网络字节序 (大端，保证跨平台一致性)
    # I 代表无符号整数 (4字节，用于 Sequence Number)
    # d 代表双精度浮点数 (8字节，用于 Timestamp)
    # 1024s 代表 1024 字节的字符流 (用于 Payload)
    return PACKET_STRUCT.pack(seq_num, timestamp, padded_payload)


def parse_packet(packet: bytes):
    """
    将接收到的二进制流拆包还原。
    """
    if len(packet) != PACKET_SIZE:
        raise ValueError(f"packet 长度应为 {PACKET_SIZE} 字节，实际为 {len(packet)} 字节")

    seq_num, timestamp, payload = PACKET_STRUCT.unpack(packet)
    # 去除 payload 尾部为了对齐而填充的空字符
    payload = payload.rstrip(b'\0')
    return seq_num, timestamp, payload


def peek_sequence(packet: bytes) -> int:
    """
    只读取报文开头的 Sequence Number，供虚拟链路做故障注入。
    """
    if len(packet) < 4:
        raise ValueError("packet 太短，无法读取 Sequence Number")
    return struct.unpack("!I", packet[:4])[0]
