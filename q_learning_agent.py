import numpy as np
import random

class QLearningAgent:
    def __init__(self, learning_rate=0.1, discount_factor=0.9, epsilon=0.2):
        # 初始化 6个状态 x 3个动作 的 Q-Table
        self.q_table = np.zeros((6, 3))
        # 轻量先验：无丢包状态倾向探测增窗，丢包状态倾向退避。
        self.q_table[[0, 2, 4], 1] = 2.0
        self.q_table[[1, 3, 5], 2] = 1.0
        
        self.lr = learning_rate      # 学习率 (Alpha)
        self.gamma = discount_factor # 折扣因子
        self.epsilon = epsilon       # 探索率 (e-greedy)
        
    def get_state_index(self, rtt_trend, loss_event):
        """
        将网络特征转化为 0-5 的状态索引
        rtt_trend: 0(变小), 1(平稳), 2(变大)
        loss_event: 0(未丢包), 1(丢包)
        """
        return rtt_trend * 2 + loss_event

    def choose_action(self, state_index):
        """
        利用 e-greedy 策略选择动作
        """
        if random.uniform(0, 1) < self.epsilon:
            # 探索：随机选择一个动作 (0, 1, 或 2)
            return random.randint(0, 2)
        else:
            # 利用：选择当前状态下 Q 值最大的动作
            return np.argmax(self.q_table[state_index])

    def learn(self, state, action, reward, next_state):
        """
        利用 Bellman 公式更新 Q-Table
        """
        best_next_action_q = np.max(self.q_table[next_state])
        td_target = reward + self.gamma * best_next_action_q
        td_error = td_target - self.q_table[state][action]
        self.q_table[state][action] += self.lr * td_error

    def calculate_reward(self, throughput_mbps, avg_rtt, loss_count):
        """
        计算复合奖励值。
        """
        # throughput_mbps 的量级通常在 0.x 到 1.x，需要比 RTT/丢包更高的正权重。
        alpha = 40.0  # 鼓励高吞吐
        beta = 5.0    # 惩罚高延迟
        gamma = 2.5   # 惩罚丢包/快重传/超时信号

        return alpha * throughput_mbps - beta * avg_rtt - gamma * loss_count

    # ================= 以下是新增的三个关键方法 =================

    def save_model(self, filename='q_table.npy'):
        """保存 Q-Table 到本地文件"""
        np.save(filename, self.q_table)
        print(f"[Agent] Q-Table 已保存至 {filename}")

    def load_model(self, filename='q_table.npy'):
        """从本地文件加载预训练的 Q-Table"""
        try:
            self.q_table = np.load(filename)
            print(f"[Agent] 成功加载预训练 Q-Table: {filename}")
        except FileNotFoundError:
            print("[Agent] 未找到预训练模型，使用全零 Q-Table 从头开始")

    def decay_epsilon(self, min_epsilon=0.01, decay_rate=0.995):
        """衰减探索率，让模型行为逐渐收敛稳定"""
        self.epsilon = max(min_epsilon, self.epsilon * decay_rate)
