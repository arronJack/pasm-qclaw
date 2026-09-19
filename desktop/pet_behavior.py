"""PetBehavior —— 桌面小人行为的「自我设计 + 自我优化」引擎（v0.28.2）。

设计哲学（对齐小志需求）：
- 小人的状态/动作**不是写死的**，而是由本引擎根据
    ① 性格原型(persona 九宫格: temper/energy/play)
    ② 成长阶段(growth 0..4，升级解锁更丰富的动作)
    ③ 用户真实反馈(夸奖/被戳/训斥/拥抱)
  来**自我设计**一套"专属行为签名"，并随使用**自我优化**权重。
- 自我设计 = design()：按「性格 × 阶段」重新生成"动作池 + 基础权重 + 表情配对"。
- 自我优化 = feedback()：用户每次反应都用 ε-贪心思路微调权重（夸了就更爱做、
  被训对吵闹动作略收），持久化到 pet_behavior.json，重启不丢、升级也不清。
- pick()：每次自主活动按当前权重抽样一个动作（含偶发探索），返回 (act, expr, caption)。

动作全集须与 pet_avatar.set_act 的合法枚举一致（v0.30.11 扩充后）：
  wave / hop / peek / ball / dance / spin / think /
  nod / stretch / arms / bounce / fly / cheer / pat / point / heart
（read/stroll 属于"真学习 / 散步"，由 pasm_pet 的自主循环负责，不进本引擎池。）
"""
from __future__ import annotations

import json
import os
import random
import time

# 动作元数据：
#   unlock  —— 第几成长阶段才进可用池（"升级解锁"的核心）
#   energy  —— 该动作的能量需求（高能量性格更爱做高 energy 动作）
#   social  —— 该动作的社交度（爱玩性格更爱做高 social 动作）
#   calm    —— 是否"安静向"动作（内向性格偏好 calm=True 的动作）
ALL_ACTS = {
    "wave":  {"unlock": 0, "energy": 0.4, "social": 0.95, "calm": True},
    "hop":   {"unlock": 0, "energy": 0.6, "social": 0.35, "calm": True},
    "peek":  {"unlock": 0, "energy": 0.2, "social": 0.50, "calm": True},
    "ball":  {"unlock": 1, "energy": 0.9, "social": 0.40, "calm": False},
    "dance": {"unlock": 2, "energy": 1.0, "social": 0.70, "calm": False},
    "spin":  {"unlock": 2, "energy": 0.85, "social": 0.55, "calm": False},
    "think": {"unlock": 3, "energy": 0.3, "social": 0.10, "calm": True},
    # —— v0.30.1 性格辨识动作 ——
    # boring=True 表示"无聊专属"：平时权重被压得很低，只在闲得慌时才冒出来
    "nod":     {"unlock": 0, "energy": 0.20, "social": 0.75, "calm": True},
    "stretch": {"unlock": 0, "energy": 0.25, "social": 0.30, "calm": True},
    "arms":    {"unlock": 1, "energy": 0.15, "social": 0.20, "calm": True},
    "bounce":  {"unlock": 1, "energy": 0.85, "social": 0.60, "calm": False},
    "fly":     {"unlock": 2, "energy": 0.95, "social": 0.10, "calm": False,
                "boring": True},
    # —— v0.30.11 动作扩充（小志反馈"小人动作少"）——
    # 4 个新动作，视觉互相区分：欢呼抬手 / 鼓掌 / 指一指 / 比心。
    # unlock 压到 0~1，让低成长阶段的小人也动得起来（此前 0 级只有 6 个动作）。
    "cheer": {"unlock": 0, "energy": 0.90, "social": 0.85, "calm": False},
    "pat":   {"unlock": 0, "energy": 0.55, "social": 0.80, "calm": True},
    "point": {"unlock": 1, "energy": 0.40, "social": 0.70, "calm": True},
    "heart": {"unlock": 1, "energy": 0.35, "social": 0.90, "calm": True},
}

# 性格原型 → 行为"种子"（自我设计的基底）。key 为 ARCH_META 的 temper 值。
#   bias   —— 该性格对各动作的初始偏好倍率（被能量/爱玩因子再缩放）
#   expr   —— 做该动作时默认搭配的表情
TEMPER_SEED = {
    "playful": {  # 调皮：爱皮、爱闹、爱球
        "bias":  {"ball": 1.6, "dance": 1.5, "spin": 1.3, "hop": 1.1,
                  "wave": 0.7, "peek": 0.5, "think": 0.4},
        "expr":  {"ball": "joy", "dance": "joy", "spin": "joy", "hop": "joy",
                  "wave": "calm", "peek": "shy", "think": "curious"}},
    "extra": {    # 外向：爱跳、爱挥手、爱热闹
        "bias":  {"dance": 1.6, "wave": 1.4, "hop": 1.2, "spin": 1.1,
                  "ball": 1.0, "peek": 0.6, "think": 0.5},
        "expr":  {"dance": "joy", "wave": "joy", "hop": "joy", "spin": "joy",
                  "ball": "joy", "peek": "curious", "think": "curious"}},
    "intro": {    # 内向：爱想、安静、偶尔探头/挥手
        "bias":  {"think": 1.7, "wave": 0.9, "peek": 0.9, "hop": 0.6,
                  "ball": 0.3, "dance": 0.3, "spin": 0.3},
        "expr":  {"think": "curious", "wave": "shy", "peek": "shy", "hop": "calm",
                  "ball": "calm", "dance": "shy", "spin": "shy"}},
    "steady": {   # 沉稳：想事、礼貌、稳步
        "bias":  {"think": 1.3, "wave": 1.0, "hop": 0.9, "peek": 0.7,
                  "ball": 0.7, "dance": 0.6, "spin": 0.6},
        "expr":  {"think": "curious", "wave": "calm", "hop": "calm",
                  "peek": "curious", "ball": "joy", "dance": "joy", "spin": "joy"}},
}

# 九种人格的**精细偏好**（v0.30.1）。
# 为什么不能只靠上面 4 个 temper：九种人格里"豪爽直率""活泼外向""好奇活泼"
# 都归 extra，"调皮灵动""机灵敏锐""毒舌损友"都归 playful —— 同一 temper 下
# 抽到的动作几乎一样，性格就没有辨识度了。这里按**人格名**再分一层。
# 未列出的动作走 design() 里的默认 0.5。
PERSONA_SEED = {
    "温和沉稳": {   # 慢热体贴：点头、伸懒腰、安静想事
        "bias": {"nod": 1.9, "stretch": 1.5, "think": 1.3, "wave": 1.0,
                 "peek": 0.8, "arms": 0.8, "hop": 0.9, "bounce": 0.4,
                 "ball": 0.5, "dance": 0.5, "spin": 0.6},
        "expr": {"nod": "calm", "stretch": "calm", "think": "curious",
                 "wave": "calm", "peek": "shy", "arms": "calm"}},
    "好奇活泼": {   # 爱闹爱笑：蹦跶、探头、挥手
        "bias": {"bounce": 1.9, "peek": 1.6, "wave": 1.4, "spin": 1.3,
                 "hop": 1.2, "nod": 1.0, "ball": 1.1, "dance": 1.2,
                 "stretch": 0.7, "arms": 0.4, "think": 0.9},
        "expr": {"bounce": "joy", "peek": "curious", "wave": "joy",
                 "spin": "joy", "hop": "joy", "nod": "joy"}},
    "机灵敏锐": {   # 反应快：转圈、探头、抱臂打量
        "bias": {"spin": 1.8, "peek": 1.5, "arms": 1.3, "bounce": 1.2,
                 "wave": 1.0, "nod": 1.0, "ball": 1.0, "dance": 0.9,
                 "stretch": 0.6, "think": 1.1, "hop": 0.9},
        "expr": {"spin": "joy", "peek": "curious", "arms": "curious",
                 "bounce": "joy", "wave": "joy"}},
    "温柔内向": {   # 安静细腻：探头、伸懒腰、抱臂
        "bias": {"peek": 2.0, "stretch": 1.4, "arms": 1.2, "think": 1.2,
                 "wave": 0.9, "nod": 1.0, "hop": 0.5, "bounce": 0.3,
                 "ball": 0.3, "dance": 0.3, "spin": 0.3},
        "expr": {"peek": "shy", "stretch": "shy", "arms": "shy",
                 "think": "curious", "wave": "shy", "nod": "shy"}},
    "活泼外向": {   # 阳光热烈：蹦跶、跳舞、挥手
        "bias": {"bounce": 2.0, "dance": 1.7, "wave": 1.5, "hop": 1.3,
                 "spin": 1.2, "nod": 1.0, "stretch": 0.6, "arms": 0.4,
                 "think": 0.5, "peek": 0.7},
        "expr": {"bounce": "joy", "dance": "joy", "wave": "joy",
                 "hop": "joy", "spin": "joy", "nod": "joy"}},
    "调皮灵动": {   # 鬼点子多：颠球、跳舞、蹦跶
        "bias": {"ball": 1.9, "dance": 1.5, "bounce": 1.4, "spin": 1.3,
                 "peek": 1.1, "hop": 1.1, "wave": 0.7, "nod": 0.7,
                 "think": 0.6, "stretch": 0.5, "arms": 0.5},
        "expr": {"ball": "joy", "dance": "joy", "bounce": "joy",
                 "spin": "joy", "peek": "joy"}},
    "沉稳可靠": {   # 踏实安心：点头、抱臂、想事
        "bias": {"nod": 1.9, "arms": 1.5, "think": 1.4, "stretch": 1.1,
                 "wave": 0.9, "peek": 0.8, "hop": 0.8, "ball": 0.6,
                 "dance": 0.5, "spin": 0.5, "bounce": 0.4},
        "expr": {"nod": "calm", "arms": "calm", "think": "curious",
                 "stretch": "calm", "wave": "calm"}},
    "豪爽直率": {   # 大大咧咧：挥手、蹦跶、点头
        "bias": {"wave": 1.9, "bounce": 1.6, "nod": 1.3, "hop": 1.2,
                 "dance": 1.1, "spin": 1.0, "ball": 0.9, "peek": 0.7,
                 "stretch": 0.9, "arms": 0.5, "think": 0.6},
        "expr": {"wave": "joy", "bounce": "joy", "nod": "joy",
                 "hop": "joy", "dance": "joy"}},
    "毒舌损友": {   # 嘴硬心软：抱臂、转圈、探头打量
        "bias": {"arms": 2.0, "spin": 1.4, "peek": 1.4, "think": 1.1,
                 "wave": 0.8, "nod": 0.8, "ball": 0.8, "dance": 0.7,
                 "hop": 0.6, "bounce": 0.5, "stretch": 0.6},
        "expr": {"arms": "proud", "spin": "proud", "peek": "curious",
                 "think": "curious", "wave": "calm"}},
}

# —— v0.30.11 动作扩充：给九种人格补上 4 个新动作的偏好与表情配对 ——
# 追加式（不动上面的嵌套字面量），既保留原结构、又让新动作带性格辨识度。
# 未列出的动作在 design() 里默认 0.5，照样能被抽到。
_NEW_ACT_BIAS = {
    "温和沉稳": {"heart": 1.5, "pat": 1.2, "point": 0.9, "cheer": 0.6},
    "好奇活泼": {"cheer": 1.6, "pat": 1.4, "point": 1.1, "heart": 1.2},
    "机灵敏锐": {"point": 1.5, "cheer": 1.1, "pat": 1.0, "heart": 0.9},
    "温柔内向": {"heart": 1.6, "pat": 1.0, "point": 0.7, "cheer": 0.5},
    "活泼外向": {"cheer": 1.9, "pat": 1.5, "heart": 1.4, "point": 0.9},
    "调皮灵动": {"point": 1.3, "cheer": 1.3, "pat": 1.2, "heart": 1.0},
    "沉稳可靠": {"pat": 1.2, "point": 1.1, "heart": 0.9, "cheer": 0.6},
    "豪爽直率": {"cheer": 1.6, "pat": 1.3, "point": 1.2, "heart": 0.7},
    "毒舌损友": {"point": 1.4, "pat": 0.8, "heart": 0.6, "cheer": 0.5},
}
_NEW_ACT_EXPR = {"cheer": "joy", "pat": "joy", "point": "curious",
                 "heart": "shy"}
for _pn, _sd in PERSONA_SEED.items():
    _sd["bias"].update(_NEW_ACT_BIAS.get(_pn, {}))
    for _a, _e in _NEW_ACT_EXPR.items():
        _sd["expr"].setdefault(_a, _e)
for _sd in TEMPER_SEED.values():
    for _a, _e in _NEW_ACT_EXPR.items():
        _sd["expr"].setdefault(_a, _e)

# 各动作默认气泡文案（被做出来时偶尔飘一句）
CAPTION = {
    "wave":  "（朝你挥了挥手）",
    "hop":   "（轻轻蹦了两下）",
    "peek":  "（悄悄探出半个脑袋）",
    "ball":  "（自己颠了颠小球）",
    "dance": "（高兴地跳了段舞）",
    "spin":  "（原地转了个圈）",
    "think": "（托着腮想了想）",
    # —— v0.30.1 ——
    "nod":     "（认真地点了点头）",
    "stretch": "（伸了个懒腰）",
    "arms":    "（抱起手臂看着你）",
    "bounce":  "（欢快地蹦跶起来）",
    "fly":     "（闲得发慌，飞起来转了个圈）",
    # —— v0.30.11 动作扩充 ——
    "cheer": "（举起双手欢呼）",
    "pat":   "（开心地拍了拍手）",
    "point": "（伸手指了指）",
    "heart": "（朝你比了个心）",
}

# 反馈对各动作的权重调整
_PRAISE_GAIN = 1.18
_POKE_GAIN = 1.10
_SCOLD_DAMP = 0.85
_HUG_GAIN = 1.12
_ADJ_CAP = 3.0
_ADJ_FLOOR = 0.3


class PetBehavior:
    """小人行为引擎：自我设计 + 自我优化，状态持久化到 pet_behavior.json。"""

    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        self._path = os.path.join(data_dir, "pet_behavior.json")
        self.state = self._load()
        # 运行期字段（可由 design 重新生成）
        self.persona = self.state.get("persona", "温和沉稳")
        self.growth = int(self.state.get("growth", 2))
        self.unlocked = list(self.state.get("unlocked", []))
        self.base = dict(self.state.get("base", {}))
        self.weights = dict(self.state.get("weights", {}))
        self.expr_for = dict(self.state.get("expr_for", {}))
        self.last_act = None
        self.epsilon = float(self.state.get("epsilon", 0.12))

    # ---------- 持久化 ----------
    def _load(self) -> dict:
        try:
            if os.path.exists(self._path):
                with open(self._path, encoding="utf-8") as f:
                    d = json.load(f)
                if isinstance(d, dict):
                    return d
        except Exception:
            pass
        return {}

    def _save(self):
        try:
            os.makedirs(self.data_dir, exist_ok=True)
            tmp = self._path + ".tmp"
            payload = {
                "persona": self.persona,
                "growth": self.growth,
                "unlocked": self.unlocked,
                "base": self.base,
                "weights": self.weights,
                "expr_for": self.expr_for,
                "adj": self.state.get("adj", {}),
                "stats": self.state.get("stats", {}),
                "epsilon": self.epsilon,
                "updated": time.strftime("%Y-%m-%d %H:%M"),
            }
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self._path)
        except Exception:
            pass

    # ---------- 自我设计 ----------
    def design(self, persona: str, growth: int, arch: dict = None) -> dict:
        """按「性格 × 成长阶段」重新设计动作池与基础权重（升级/换性格时调用）。

        已通过 feedback() 累积的用户偏好(adj)会被保留并重新叠加，
        所以"自我优化成果"不会因升级或换性格被清零。
        """
        self.persona = persona
        self.growth = int(growth)
        if arch is None:
            arch = {}
        temper = (arch.get("temper") or "steady")
        energy = float(arch.get("energy", 0.35))
        play = float(arch.get("play", 0.35))
        # 优先按**人格名**（九种）取精细偏好；取不到才退到 temper（四种）。
        # 这样"豪爽直率"和"活泼外向"抽到的动作才会真的不一样。
        seed = (PERSONA_SEED.get(persona)
                or TEMPER_SEED.get(temper, TEMPER_SEED["steady"]))

        base = {}
        for act, m in ALL_ACTS.items():
            if m["unlock"] > self.growth:
                continue  # 未解锁 → 暂不进可用池
            w = float(seed["bias"].get(act, 0.5))
            # 能量高 → 高能量动作更受青睐；爱玩 → 高社交动作更受青睐
            if m["energy"] >= 0.7:
                w *= (0.6 + energy * 0.9)
            if m["social"] >= 0.7:
                w *= (0.7 + play * 0.7)
            base[act] = round(max(0.05, w), 3)

        self.unlocked = list(base.keys())
        self.base = base
        self.expr_for = dict(seed["expr"])
        # 叠加用户历史反馈（保留优化成果）
        adj = self.state.setdefault("adj", {})
        self.weights = {a: round(base[a] * float(adj.get(a, 1.0)), 3) for a in base}
        self.state["persona"] = persona
        self.state["growth"] = self.growth
        self.state["unlocked"] = self.unlocked
        self.state["base"] = self.base
        self.state["weights"] = self.weights
        self.state["expr_for"] = self.expr_for
        self._save()
        return {"unlocked": self.unlocked, "weights": self.weights,
                "expr_for": self.expr_for}

    # ---------- 采样（自主活动用） ----------
    def pick(self, mood: str = None, rng: random.Random = None, boring=False):
        """按当前权重抽一个动作，返回 (act, expr, caption)；无可用池返回 None。

        mood="happy" 时，若默认表情是 calm 则升格为 joy（被夸/开心场景更生动）。
        ε-探索：以 epsilon 概率随机选一个动作，避免行为随时间僵化。
        """
        rng = rng or random
        if not self.weights:
            return None
        acts = list(self.weights.keys())
        # 无聊专属动作（如 fly 飞天转圈）：平时压到 0.12 倍，**闲得慌时放大 6 倍**。
        # 这样它不会在正常陪伴中乱冒，而是真正的"自娱自乐"。
        w = []
        for _a in acts:
            _w0 = self.weights[_a]
            if ALL_ACTS.get(_a, {}).get("boring"):
                _w0 *= 6.0 if boring else 0.12
            w.append(_w0)
        if rng.random() < self.epsilon:
            act = rng.choice(acts)                      # 探索：试试冷门动作
        else:
            total = sum(w)
            if total <= 0:
                act = rng.choice(acts)
            else:
                r = rng.random() * total
                acc = 0.0
                act = acts[-1]
                for a, aw in zip(acts, w):
                    acc += aw
                    if r <= acc:
                        act = a
                        break
        self.last_act = act
        self._note(act)
        expr = self.expr_for.get(act, "calm")
        if mood == "happy" and expr == "calm":
            expr = "joy"
        return (act, expr, CAPTION.get(act, ""))

    # ---------- 自我优化（用户反馈） ----------
    def feedback(self, kind: str, act: str = None):
        """用户一次真实反应 → 微调权重并持久化。

        kind: praise(夸奖) / poke(被戳) / scold(被训) / hug(拥抱)
        夸奖 → 刚做的动作权重上调；被戳 → 互动类动作(wave/hop)小幅上调；
        被训 → 高能量吵闹动作(ball/dance/spin)略收（非清零，避免行为崩坏）；
        拥抱 → 活泼类动作小幅上调。
        """
        act = act or self.last_act
        if not act or act not in ALL_ACTS:
            return
        adj = self.state.setdefault("adj", {})
        cur = float(adj.get(act, 1.0))
        m = ALL_ACTS.get(act, {})
        if kind == "praise":
            cur = min(_ADJ_CAP, cur * _PRAISE_GAIN)
        elif kind == "poke":
            if act in ("wave", "hop"):
                cur = min(_ADJ_CAP, cur * _POKE_GAIN)
        elif kind == "scold":
            if m.get("energy", 0) >= 0.8:
                cur = max(_ADJ_FLOOR, cur * _SCOLD_DAMP)
        elif kind == "hug":
            if act in ("wave", "hop", "dance"):
                cur = min(_ADJ_CAP, cur * _HUG_GAIN)
        else:
            return
        adj[act] = round(cur, 3)
        self.state["adj"] = adj
        if self.base:
            self.weights = {a: round(self.base[a] * float(adj.get(a, 1.0)), 3)
                            for a in self.base}
        self._save()

    def _note(self, act: str):
        st = self.state.setdefault("stats", {})
        st[act] = int(st.get(act, 0)) + 1
        self.state["stats"] = st
        # 统计信息不频繁落盘，pick 多次才存一次由调用方决定；这里直接存开销小

    def summary(self) -> str:
        """给"最近动态 / 调试"用的可读摘要。"""
        top = sorted(self.weights.items(), key=lambda x: x[1], reverse=True)
        rows = "，".join(f"{a}×{w:.2f}" for a, w in top[:5])
        return (f"性格[{self.persona}] 阶段{self.growth} 解锁{self.unlocked} "
                f"偏好Top {rows}")


# ---------------- 自检 ----------------
def _selftest():
    import tempfile
    d = tempfile.mkdtemp(prefix="petbeh_")
    ok = True
    msgs = []

    def chk(name, cond):
        nonlocal ok
        ok = ok and cond
        msgs.append(("OK " if cond else "FAIL") + " " + name)

    # 解锁口径随 ALL_ACTS 变化自动同步（避免动作集扩充后自检写死数字而 FAIL）
    expect_full = [a for a, m in ALL_ACTS.items() if m["unlock"] <= 4]
    expect_l0 = [a for a, m in ALL_ACTS.items() if m["unlock"] <= 0]
    for temper, persona in (("playful", "调皮灵动"), ("extra", "活泼外向"),
                            ("intro", "温柔内向"), ("steady", "温和沉稳")):
        # 用与 ARCH_META 一致的字段构造 arch
        arch = {"temper": temper, "energy": 0.7, "play": 0.7}
        if temper == "intro":
            arch = {"temper": "intro", "energy": 0.22, "play": 0.15}
        elif temper == "steady":
            arch = {"temper": "steady", "energy": 0.35, "play": 0.2}
        b = PetBehavior(d)
        b.design(persona, 4, arch)
        chk(f"{persona}: 满级解锁{len(expect_full)}动作",
            set(b.unlocked) == set(expect_full))
        # 低阶段应解锁更少
        b.design(persona, 0, arch)
        chk(f"{persona}: 0级只解锁基础动作",
            set(b.unlocked) == set(expect_l0))
        b.design(persona, 4, arch)

    # 权重采样符合偏好（高权重动作出现更多）
    b = PetBehavior(d)
    b.design("调皮灵动", 4, {"temper": "playful", "energy": 0.7, "play": 0.7})
    cnt = {}
    for _ in range(4000):
        r = b.pick()
        cnt[r[0]] = cnt.get(r[0], 0) + 1
    chk("调皮: ball 出现次数 > think", cnt.get("ball", 0) > cnt.get("think", 0))
    chk("采样不出未解锁动作", all(a in b.unlocked for a in cnt))

    # 反馈：夸奖后权重上升
    before = b.weights["ball"]
    b.feedback("praise", "ball")
    after = b.weights["ball"]
    chk("夸奖提升 ball 权重", after > before)

    # 反馈：被训后高能量动作下降
    b2 = PetBehavior(d)
    b2.design("调皮灵动", 4, {"temper": "playful", "energy": 0.7, "play": 0.7})
    b2_before = b2.weights["dance"]
    b2.feedback("scold", "dance")
    chk("被训降低 dance 权重", b2.weights["dance"] < b2_before)

    # 持久化：重建后保留 adj
    b3 = PetBehavior(d)
    chk("重载保留 ball 高权重", b3.weights.get("ball", 0) >= before - 1e-6)

    # 升级不清反馈：升级后再 design，ball 仍高
    b3.design("调皮灵动", 4, {"temper": "playful", "energy": 0.7, "play": 0.7})
    chk("升级保留反馈优化", b3.weights.get("ball", 0) >= before - 1e-6)

    print("\n".join(msgs))
    print("RESULT:", "PASS" if ok else "FAIL")
    return ok


if __name__ == "__main__":
    _selftest()
