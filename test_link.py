import time
from protocol import make_packet
from virtual_link import VirtualLink

def test_congestion():
    # 假设接收端在本地 8888 端口（此时并不需要真有个接收端，我们只测发送端队列）[cite: 41]
    link = VirtualLink("127.0.0.1", 8888)
    
    print("开始瞬间发送 30 个数据包...")
    for i in range(1, 31):
        # 生成测试报文
        packet = make_packet(seq_num=i, payload_data=f"Test data {i}".encode('utf-8'))
        # 塞入虚拟链路
        link.send(packet)
        
    print("瞬间发送完毕！你可以观察到前面 20 个包悄悄排队发出去了，而后面 10 个包触发了丢弃机制。")
    print(f"虚拟链路统计到的丢包数：{link.drain_dropped_count()}")
    
    # 让主线程稍微等一会儿，保证后台漏斗线程能把队列里的 20 个包慢悠悠地发完
    time.sleep(1)
    link.close()

if __name__ == "__main__":
    test_congestion()
