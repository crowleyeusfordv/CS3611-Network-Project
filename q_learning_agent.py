import numpy as np
import random

class QLearningAgent:
    def __init__(self, learning_rate=0.1, discount_factor=0.9, epsilon=0.2):
        # 初始化 6个状态 x 3个动作 的 Q-Table
        self.q_table = np.zeros((6, 3))
        
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

    def calculate_reward(self, throughput, avg_rtt, loss_count):
        """
        计算复合奖励值。
        """
        # 增大吞吐奖励权重，让策略更愿意探测更高 CWND 区间。
        alpha = 2.2   # 鼓励高吞吐
        # 你现在的 reward 里，“丢包惩罚”占比过高，容易让 Q-learning 在高 CWND 附近
        # 因少量偶发丢包而迅速学习到“立刻减半”的保守策略。
        # 这里降低延迟/丢包的惩罚权重，让策略更平滑地逼近拥塞边缘。
        beta = 3.5    # 惩罚高延迟（进一步降，减少因 RTT 尖峰导致的频繁减半）
        gamma = 4.0   # 惩罚丢包（进一步降，允许更积极的 CWND 探测）
        
        return alpha * throughput - beta * avg_rtt - gamma * loss_count

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