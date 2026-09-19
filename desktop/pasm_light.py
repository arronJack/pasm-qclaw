"""pasm_light —— PASM 引擎的可选轻量替代（无 torch/numpy 依赖）。

设计背景：PASM 完整引擎依赖 torch（科研训练向）。为了让安装包保持在
~100MB 量级、同时让**没有装 torch 的环境也能跑桌面版**，聊天窗在
`import pasm.agent` 失败时自动回退到本模块——接口与完整引擎对齐
（PASMAgent.act/learn/reset_episode/snapshot、GridWorld、PASMConfig），
内部用纯 Python 规则模拟"情绪向量 + 性格 + 记忆计数 + 发育阶段"。

语义约定（与完整引擎一致）：
  - personality: openness/caution/sociability ∈ [-1,1]，随相处缓慢漂移
  - emotion:     valence/serotonin/arousal/dominance/dopamine 持续值
  - snapshot():  返回 companion/offline_brain 需要的全部字段
装了 torch 的机器仍走完整引擎，本模块不会被用到。
"""
from __future__ import annotations

import random


class PASMConfig:
    """接受任意关键字配置的轻量配置对象（兼容引擎构造调用）。"""

    def __init__(self, **kw):
        self.seed = int(kw.get("seed", 0))
        for k, v in kw.items():
            setattr(self, k, v)

    def to_dict(self) -> dict:
        return {k: v for k, v in vars(self).items() if not k.startswith("_")}


def _clamp(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


class GridWorld:
    """微型"世界"：4 维观测上做随机游走，给情绪一个动力学载体。"""

    DIM = 4

    def __init__(self, seed: int = 0):
        self.rng = random.Random(seed)
        self.pos = [0.0] * self.DIM

    def reset(self):
        self.pos = [self.rng.uniform(-0.3, 0.3) for _ in range(self.DIM)]

    def _get_obs(self) -> list:
        return list(self.pos)

    def step(self, action: int):
        a = int(action) % self.DIM
        self.pos[a] = _clamp(self.pos[a] + self.rng.uniform(-0.25, 0.25))
        reward = self.rng.uniform(-0.1, 0.1)
        return list(self.pos), reward, False, {}


class PASMAgent:
    """轻量认知体：维持性格/情绪/记忆计数，接口对齐完整 PASMAgent。"""

    def __init__(self, config=None, personality_seed=None, device="cpu"):
        self.cfg = config or PASMConfig(seed=0)
        self.rng = random.Random(getattr(self.cfg, "seed", 0) or 0)
        seed = list(personality_seed or [0.4, 0.2, 0.5])
        while len(seed) < 3:
            seed.append(0.3)
        self._openness = _clamp(float(seed[0]))
        self._caution = _clamp(float(seed[1]))
        self._sociability = _clamp(float(seed[2]))
        self.valence = 0.05
        self.serotonin = 0.5
        self.arousal = 0.3
        self.dominance = 0.5
        self.dopamine = 0.0
        self.total_steps = 0
        self.episodic = 0
        self.habits = 0
        self._plasticity = 0.85
        self._epoch = 0

    # ---- 决策循环（对齐完整引擎调用契约） ----
    def reset_episode(self):
        self._epoch += 1
        # 每个"会话/成长轮"后发育可塑性缓慢下降（长定型）
        self._plasticity = max(0.35, self._plasticity - 0.012)

    def act(self, obs, learning: bool = True):
        return self.rng.randrange(4), {}

    def learn(self, obs, action, next_obs, reward, report=None):
        self.total_steps += 1
        r = float(reward)
        # 情绪动力学：愉悦向奖励靠拢 + 缓慢回归基线
        self.valence = _clamp(self.valence * 0.90 + r * 0.55
                              + self.rng.gauss(0, 0.015))
        self.serotonin = _clamp(self.serotonin * 0.96
                                + (0.5 - self.serotonin) * 0.02 + r * 0.02,
                                0.0, 1.0)
        self.arousal = _clamp(self.arousal + abs(r) * 0.1 - 0.01, 0.0, 1.0)
        self.dopamine = _clamp(self.dopamine * 0.9 + r * 0.5)
        # 性格微漂移：正向相处 → 更开放/亲社交；负面 → 更谨慎
        self._openness = _clamp(self._openness + r * 0.004
                                + self.rng.gauss(0, 0.002))
        self._caution = _clamp(self._caution - r * 0.003)
        self._sociability = _clamp(self._sociability + r * 0.003)
        # 显著事件 → 记一段"情景"
        if abs(r) > 0.05 and self.rng.random() < 0.6:
            self.episodic += 1
        if r > 0.5 and self.rng.random() < 0.5:
            self.habits += 1
        return {"wrote_memory": False}

    def freeze_vae(self):
        pass

    def save(self, path: str):
        raise NotImplementedError("轻量引擎无需存档（情绪随 pet_state 成长）")

    def load(self, path: str):
        raise NotImplementedError

    # ---- 状态导出 ----
    def snapshot(self) -> dict:
        return {
            "step": self.total_steps,
            "personality": {"openness": round(self._openness, 3),
                            "caution": round(self._caution, 3),
                            "sociability": round(self._sociability, 3)},
            "emotion": {"valence": round(self.valence, 3),
                        "serotonin": round(self.serotonin, 3),
                        "arousal": round(self.arousal, 3),
                        "dominance": round(self.dominance, 3),
                        "dopamine": round(self.dopamine, 3)},
            "development": {"plasticity": round(self._plasticity, 3),
                            "stage": self.stage_name},
            "memory": {"episodic": self.episodic, "habits": self.habits},
            "global_workspace": None,
            "last_retrieval": None,
        }

    @property
    def stage_name(self) -> str:
        if self._plasticity > 0.7:
            return "幼儿期"
        if self._plasticity > 0.55:
            return "童年期"
        if self._plasticity > 0.45:
            return "少年期"
        return "青年期"


if __name__ == "__main__":
    a = PASMAgent(PASMConfig(seed=1, plan_samples=8, plan_iters=1),
                  personality_seed=[0.8, -0.2, 0.6])
    e = GridWorld(seed=1)
    e.reset()
    for i in range(60):
        obs = e._get_obs()
        act, rep = a.act(obs)
        nxt, r, done, _ = e.step(act)
        a.learn(obs, act, nxt, r, rep)
        if done:
            e.reset(); a.reset_episode()
    s = a.snapshot()
    print("emotion:", s["emotion"])
    print("personality:", s["personality"])
    print("stage:", s["development"]["stage"], "| episodic:", s["memory"]["episodic"])
