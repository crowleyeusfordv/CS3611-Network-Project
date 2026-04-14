当然可以！我已经按照你的要求，将这份大作业需求文档转化成了标准的 **Markdown** 格式。你可以直接将其复制到任何支持 Markdown 的编辑器中（如 Typora、VS Code 或 Obsidian）。

---

# 计算机网络 CS3611 大作业
## [cite_start]题目五：基于 UDP 的应用层可靠传输与 AI 驱动拥塞控制协议实现 [cite: 1]

### [cite_start]1. 背景介绍 [cite: 2]
[cite_start]由于 TCP 协议固化在操作系统内核中，修改门槛极高，因此谷歌等公司提出了基于 UDP 的 **QUIC 协议**，将可靠传输和拥塞控制移至应用层 [cite: 3]。

[cite_start]本实验要求模拟 QUIC 的思想，基于纯 UDP Socket，在 Python 应用层从零实现一个带有“确认重传（ACK）”和“拥塞窗口（CWND）”机制的可靠传输协议 [cite: 3][cite_start]。学生将废弃传统的 AIMD（加性增乘性减）规则，引入强化学习 **Q-Learning 算法**，在线训练一个智能发送端 [cite: 3][cite_start]。发送端通过实时监测 RTT 和丢包情况，利用 Q-Table 动态调整发送速率，实现高吞吐、低延迟的传输 [cite: 3]。

---

### [cite_start]2. 实验目标 [cite: 4]
* [cite_start]**深入理解内核协议**：掌握基于原生 UDP 在应用层封装可靠传输机制 [cite: 5]。
* [cite_start]**构建核心机制**：掌握窗口机制 (CWND)、滑动队列与重传超时 (RTO) 的构建方法 [cite: 6]。
* [cite_start]**数字化表征**：实践 RTT 平滑采集与丢包率统计 [cite: 7]。
* [cite_start]**综合应用策略**：应用经典 AIMD 基线与前沿强化学习 (Q-Learning) 动态控制策略 [cite: 8]。

---

### [cite_start]3. 实验要求（基础功能 - 必做） [cite: 9, 10]

#### [cite_start]3.1 应用层可靠传输协议建立 [cite: 11]
* [cite_start]**自定义封包**：设计报文格式为：`Sequence Number (4字节)` + `Timestamp (8字节)` + `Payload (1024字节)` [cite: 12]。
* [cite_start]**确认与超时重传**：接收方收到包后回复 ACK；发送方维护“未确认队列”并开启定时器，若超过 RTO 未收到 ACK 则触发重传 [cite: 13]。
* [cite_start]**RTT 采样**：利用报文中的 Timestamp 计算 RTT，并平滑计算 **SRTT** [cite: 14]。

#### [cite_start]3.2 虚拟瓶颈链路封装 [cite: 15]
[cite_start]在 `sendto()` 外层构建模拟器，设定固定带宽（如 100包/秒）和有限队列（如最大缓存 20个包） [cite: 16, 17]。
* [cite_start]**产生真实延迟（排队）**：当瞬间发送量超过队列时，数据包需在虚拟队列中排队，模拟拥塞导致的 RTT 飙升 [cite: 19]。
* [cite_start]**产生拥塞丢包（溢出）**：若虚拟队列已满，后续数据包将被直接抛弃 [cite: 19]。

#### [cite_start]3.3 实现传统 AIMD 拥塞控制基线 [cite: 20]
[cite_start]实现类似 TCP Reno 的规则作为对照组 [cite: 21]：
* [cite_start]**初始化**：CWND = 1 [cite: 22]。
* [cite_start]**加性增**：每收到一个 ACK，`CWND += 1/CWND` [cite: 23]。
* [cite_start]**乘性减**：发生超时丢包时，`CWND = max(1, CWND/2)` [cite: 24]。

#### [cite_start]3.4 Q-Learning 智能拥塞控制器设计 [cite: 26]
* [cite_start]**状态空间 (State)**：将过去 1 个 RTT 内的网络特征离散化为 6 个状态：RTT 趋势 × 丢包事件 [cite: 28]。
* [cite_start]**动作空间 (Action)**：0 (保持), 1 (CWND+1), 2 (CWND/2) [cite: 29]。
* [cite_start]**奖励函数 (Reward)**：设计复合奖励公式，如：$R = \alpha \times \text{本轮成功吞吐量} - \beta \times \text{平均 RTT} - \gamma \times \text{丢包数量}$ [cite: 30]。
* [cite_start]**在线学习**：利用 $\epsilon\text{-greedy}$ 策略探索，并使用 Bellman 公式实时更新 Q-Table [cite: 31]。

#### [cite_start]3.5 数据记录与可视化 [cite: 32]
[cite_start]使用 `matplotlib` 绘制以下图表 [cite: 33]：
* [cite_start]CWND 随时间的变化曲线图（对比 AIMD 的“锯齿波”与 Q-Learning 的“平滑波”） [cite: 34]。
* [cite_start]吞吐量与延迟柱状图 [cite: 34]。

---

### [cite_start]4. 扩展功能 [cite: 35]
1.  [cite_start]**动态网络突变响应**：传输中途人为减半带宽，观察 AI 的恢复能力 (10分) [cite: 36]。
2.  [cite_start]**快速重传与乱序处理**：实现“3个重复 ACK 立即触发重传”机制 (15分) [cite: 37]。
3.  [cite_start]**深度强化学习 (DQN/PPO)**：利用 PyTorch 构建神经网络模型处理连续状态输入 (25分) [cite: 38]。
4.  [cite_start]**自定义开放性问题**：提出并实现与网络架构或通信效率相关的扩展方案 (10-25分) [cite: 39]。

---

### [cite_start]5. 提交内容 [cite: 51]
* [cite_start]**代码**：发送端/接收端源码、虚拟链路类、预训练模型/权重文件 [cite: 52, 53, 54]。
* [cite_start]**文档**：设计文档（图解协议头、奖励函数模型）、测试报告（收敛曲线、对比数据）、操作手册 [cite: 55, 56, 57, 58]。
* [cite_start]**演示**：现场展示重传与拥塞控制功能的有效性，并对比展示 CWND 演变曲线 [cite: 59, 60, 61]。