# CS3611 UDP 可靠传输与 AI 拥塞控制

本项目基于原生 UDP Socket，在 Python 应用层实现可靠传输、RTO 重传、CWND 拥塞窗口、AIMD 基线与 Q-Learning 拥塞控制，并加入动态带宽突变和快速重传/乱序处理扩展。

## 环境

```bash
pip install -r requirements.txt
```

## 运行方式

先开接收端终端：

```bash
python receiver.py --host 127.0.0.1 --port 8888
```

再开一个发送端终端运行完整对照实验：

```bash
python sender.py --controller both --packets 2000 --dynamic --drop-once 80 --output-dir .
```

常用参数：

- `--controller aimd|q|both`：选择 AIMD、Q-Learning 或顺序运行两者。
- `--packets 2000`：发送数据包数量。
- `--dynamic`：开启动态网络突变，默认第 8 秒把带宽从 100pps 降到 50pps。
- `--drop-once 80`：首次发送指定序号时丢弃一次，用于演示重复 ACK 与快速重传。
- `--output-dir .`：输出图表和 `summary.json` 的目录。
- `--no-save-model`：测试时不覆盖 `q_table.npy`。

## 输出文件

运行结束后会生成或更新：

- `aimd_result.png`：AIMD 的 CWND 曲线。
- `q_learning_result.png`：Q-Learning 的 CWND 曲线。
- `cwnd_compare.png`：两种算法的 CWND 对照曲线。
- `throughput_delay_bar.png`：两种算法的平均吞吐量与平均 RTT 柱状图。
- `summary.json`：本次实验的平均 Mbps、平均 RTT、丢包、超时、快速重传等统计。
- `q_table.npy` / `q_table_best_stats.json`：Q-Learning 训练后的 Q-Table 与最佳记录。

## 快速验证

```bash
python -m py_compile protocol.py virtual_link.py q_learning_agent.py receiver.py sender.py test_link.py
python test_link.py
```

`test_link.py` 会瞬间发送 30 个包，在默认 20 个队列容量下触发 10 次虚拟链路溢出丢包。
