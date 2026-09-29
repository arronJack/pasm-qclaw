"""pasm2_voice —— 桌面端 V2 认知引擎的「感知 + 自然表达」层（实验，v0.31.13）。

为什么需要它（2026-09-29 复盘发现的三个真实缺口）
--------------------------------------------------
1. **引擎学不到对话**：`_grow()` 只喂 GridWorld 的随机观测，用户说的话从没进过
   认知引擎 → V2 的上下文预测在真实对话里"没料可预测"。
2. **V2 不参与回复**：回复要么走 LLM，要么走 `offline_brain.py` 的关键词预设句表
   （`MOOD`/`ASK`/`TRAITS` 固定答句 + `if any(w in text ...)` 分支）。
3. **从不睡眠**：桌面从不调引擎的 `sleep()` → 符号化/记忆图巩固/DMN 永不运行。

本模块把这三条补上，原则是**"状态驱动，而不是问答表"**：
- `TextEncoder`：中文（字 + 双字组，去停用词）+ 英文词的**特征哈希**编码。
  同一句话必得同一向量；措辞相近的句子会自然靠拢（参数经实验标定，见
  `CONV_ENGINE_KW` 注释），因此"同一个话题被反复提起"能聚成同一象量。
- `Pasm2Voice.perceive()`：把用户这一句真正喂进认知引擎（`observe` + `tell`
  外部指称），让预测器/记忆图/符号化在真实对话中积累。
- `Pasm2Voice.digest()`：读出引擎**当前真有的**认知状态（情绪/躯体标记、
  预测分布、记忆图联想、自传体情节、符号、双系统慢思）。**引擎没有的一律
  None，绝不编造**。
- `Pasm2Voice.compose()`：**无 LLM** 时的自然表达 —— 由上面的状态决定"说什么、
  用什么语气"，句式池按状态加权抽样并做近期去重，因此同一句输入不会每次
  回一模一样的句子。内容只引用引擎里真有的词与经历（`known_terms()` 白名单）；
  三角校验不过就**如实说拿不准**。

能力边界（诚实申报，别吹）
--------------------------
V2 的"上下文预测"是**象量序列**层面的条件分布（符号化之前），不是语言模型：
它不会百科问答，也不能凭空生成任意自然语言。它能做到的是——**记得住、串得起
关联、有真实情绪、会如实说不知道**。所以无 LLM 档的定位是"一个记得你的本地
伙伴"，而非"小百科"。要知识广度仍需 LLM；本层的作用是**让 LLM 那个档也更懂你**，
以及**在没有 LLM 时也不至于退化成关键词答句表**。
"""
from __future__ import annotations

import hashlib
import random
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

__all__ = ["TextEncoder", "Pasm2Voice", "CONV_ENGINE_KW", "CONV_LATENT_DIM"]

#: 对话感知专用的引擎参数（**只影响桌面 V2 会话**，内核 brainwide profile 不变）。
#:
#: 为什么需要：内核默认 `entity_latent_dim=8 / create_threshold=0.72` 是按
#: "随机初始化潜空间 + 余弦" 标定的；而**文本特征哈希余弦的尺度低得多**
#: （相近句 ~0.3-0.9，无关句 ~0.03-0.4）。直接套用会让每句话都变成新象量，
#: 记忆与预测都长不出来。2026-09-29 实验标定（8 话题 × 4 种说法，见
#: docs/pasm2/V2-EXPRESSION.md）：潜维 128 + 阈值 0.22 时类间均值 ≈ 0.05，
#: 同话题**词面重合够多**的句子（同句 / 加语气词 / 换同义说法）能聚成一类；
#: 潜维 8/16/32/64 的哈希碰撞太多（无关句被误并成同一概念）。
#:
#: ⚠️ 诚实边界：这是**词面**模型，没有真嵌入。字面毫无交集的两句同话题话
#: （如「猫很黏人」vs「我家猫咪很可爱」）**分不到一类** —— 这是固有上限，
#: 需真嵌入模型才能解决，留给 V2.x。因此表达层**不依赖聚类完美**：它同时用
#: 用户原话、记忆图邻居、自传体情节、情绪与预测多路证据，聚类略糙只表现为
#: "某个概念积累得慢一点"，不会让回复崩坏。
CONV_LATENT_DIM = 128
CONV_ENGINE_KW: Dict[str, Any] = {
    "entity_latent_dim": CONV_LATENT_DIM,
    "entity_create_threshold": 0.22,
    "predictor_min_samples": 2,      # 对话轮数少，门槛降一档才给得出预测
    "predictor_confidence_gate": 0.40,
}

_CJK = re.compile(r"[\u4e00-\u9fff]")
_WORD = re.compile(r"[a-zA-Z]+|\d+")
#: 停用字表（**必须只放单字功能字**）：这些字作特征会把所有句子拉平。
#: ⚠️ 2026-09-29 踩坑：起初误把「如果/还是/知道/今天」这类**多字词**写进来，
#: 拆成单字后「果/知/道/天」就成了虚词，连「苹果」都被降权（topic() 选出
#: 「吃苹」这种词）。多字词的判定交给双字组本身，别在这里替它做。
_STOP = set("的了是我你他她它们在有不都很也而且还但所以为这那什么怎么么呢啊吧"
            "把被给对从到要会能可以去着过没又太真呀哦其与或之就个们得")

#: 情绪 → 语气档（compose 用；不是答句表，只是"用什么腔调开口"）
_MOOD_BANDS = ("up", "calm", "low")


def _band(valence: float, arousal: float = 0.0) -> str:
    if valence > 0.25 or (valence > 0.1 and arousal > 0.5):
        return "up"
    if valence < -0.25:
        return "low"
    return "calm"


class TextEncoder:
    """文本 → 定长向量（特征哈希，确定性，numpy-only）。

    特征：相邻汉字**双字组**（主特征）+ 英文/数字词 + （可选）非虚词单字。
    ⚠️ 单字默认**关**（`use_chars=False`）：单字是"假重叠"的主要来源——
    「我喜欢听音乐」和「我喜欢吃苹果」会因共享 我/喜/欢 三字而算得很像；
    去掉后类间余弦均值从 0.11 降到 0.05（2026-09-29 实测），话题区分更干净。
    """

    def __init__(self, dim: int = CONV_LATENT_DIM, w_bigram: float = 1.0,
                 w_word: float = 1.5, use_chars: bool = False) -> None:
        self.dim = max(8, int(dim))
        self.w_bigram = float(w_bigram)
        self.w_word = float(w_word)
        self.use_chars = bool(use_chars)
        #: 跨轮词频（判断"这是不是用户真在反复说的词"，用于过滤偶然碎片）
        self._term_freq: Dict[str, int] = {}

    # ---- 内部 ----
    @staticmethod
    def _hash(s: str, mod: int) -> int:
        return int.from_bytes(hashlib.md5(s.encode("utf-8")).digest()[:4],
                              "big") % mod

    def features(self, text: str) -> List[Tuple[str, float]]:
        t = text or ""
        han = _CJK.findall(t)
        feats: List[Tuple[str, float]] = []
        if self.use_chars:
            feats += [(c, 1.0) for c in han if c not in _STOP]
        feats += [("".join(han[i:i + 2]), self.w_bigram)
                  for i in range(len(han) - 1)]
        feats += [(w.lower(), self.w_word) for w in _WORD.findall(t)]
        return feats

    def encode(self, text: str) -> np.ndarray:
        v = np.zeros(self.dim, dtype=np.float64)
        for f, w in self.features(text):
            v[self._hash(f, self.dim)] += w
        n = float(np.linalg.norm(v))
        return v / n if n > 1e-9 else v

    #: 停用词字在"话题词"打分里的权重（虚词参与的二字组多半是噪声）
    _STOP_CHAR_W = 0.15

    def _char_w(self, c: str) -> float:
        return self._STOP_CHAR_W if c in _STOP else 1.0

    def topic(self, text: str) -> str:
        """从用户这句话里挑一个"最像话题"的词（双字组优先，其次英文词）。

        这是**用户的词**，不是我们编的 —— compose 里引用它是安全的。
        打分 = 字权重之积（虚词字降权）×（1 + 在本句出现次数），并列取更靠后者
        （中文里话题性词多落在句子后半）。
        """
        t = text or ""
        han = _CJK.findall(t)
        cand: List[Tuple[float, int, str]] = []
        for i in range(len(han) - 1):
            b = han[i] + han[i + 1]
            # 跨轮词频是"这个词是不是话题"的最强信号（用户反复说的才算）
            sc = (1.0 + self._term_freq.get(b, 0)) \
                * self._char_w(han[i]) * self._char_w(han[i + 1]) \
                * (1.0 + t.count(b))
            cand.append((sc, i, b))
        for i, w in enumerate(_WORD.findall(t)):
            wl = w.lower()
            cand.append((self.w_word * (1.0 + t.count(w)), i, wl))
        if not cand:
            single = [c for c in han if c not in _STOP]
            return single[0] if single else ""
        cand.sort(key=lambda x: (-x[0], -x[1]))
        return cand[0][2]

    def keywords(self, text: str, k: int = 3) -> List[str]:
        """挑 k 个**可读**词做外部指称（tell 用）。

        只用「两个字都不是虚词」的双字组 —— 保证是可读词而不是「我喜」这种
        跨界碎片；再按**跨轮累计词频**优先（用户反复说的才是真词，
        「吃苹」这种偶然碎片排后面）。这直接影响符号与 SLH 映射的质量。
        """
        t = text or ""
        han = _CJK.findall(t)
        cand: List[Tuple[float, int, str]] = []
        for i in range(len(han) - 1):
            if self._char_w(han[i]) < 1.0 or self._char_w(han[i + 1]) < 1.0:
                continue
            b = han[i] + han[i + 1]
            # 打分：跨轮累计频次（真词） > 本句重数 > 靠后位置
            cand.append((self._term_freq.get(b, 0.0) * 2.0 + t.count(b), i, b))
        cand.sort(key=lambda x: (-x[0], -x[1]))
        out: List[str] = []
        for _, _, b in cand:
            if b not in out:
                out.append(b)
            if len(out) >= k:
                return out
        for w in _WORD.findall(t):
            wl = w.lower()
            if wl not in out:
                out.append(wl)
            if len(out) >= k:
                return out
        for c in han:                          # 兜底：非虚词单字
            if c not in _STOP and c not in out:
                out.append(c)
            if len(out) >= k:
                break
        return out

    def count_terms(self, text: str) -> None:
        """累计跨轮词频（判断"这是不是用户反复说的真词"）。"""
        han = _CJK.findall(text or "")
        for i in range(len(han) - 1):
            if self._char_w(han[i]) < 1.0 or self._char_w(han[i + 1]) < 1.0:
                continue
            b = han[i] + han[i + 1]
            self._term_freq[b] = self._term_freq.get(b, 0) + 1

    def freq(self, term: str) -> int:
        """某词的跨轮累计出现次数（0 = 没见过）。"""
        return int(self._term_freq.get(term, 0))

    def prefer_real(self, terms: Sequence[str]) -> List[str]:
        """展示用词优选："用户真在反复说"的词优先，全是碎片才退而求其次。

        （2026-09-29：'天加'（今天|加班的跨界碎片）这类会被 tell 当指称收进
        词表，读起来很怪 —— 有真词就只用真词。）
        """
        uniq: List[str] = []
        for t in terms:
            if t and t not in uniq:
                uniq.append(t)
        real = [t for t in uniq if self.freq(t) >= 2]
        return real or uniq

    def real_word(self, term: str) -> bool:
        """是不是"用户真在反复说"的词（≥2 轮），用于过滤偶然碎片。"""
        return self.freq(term) >= 2


# ---------------------------------------------------------------- 表达池
# 说明：这些是**句式骨架**（怎么开口/怎么收束），不是"问什么答什么"的答句表。
# 槽位 {t}=话题词 {h}=听到的话 {e}=经历 {g}=躯体直觉 {p}=预测项，
# 全部由引擎真值填充；引擎里没有对应内容时该骨架**不会被选中**。
_OPEN = {
    "up": ["嗯！听你这么说我心里都亮了一下——", "哈，我现在的情绪值是往上飘的——",
           "你这句让我有点开心，", "我这边心情正好着，", "说起来我还有点兴奋呢，"],
    "calm": ["我在听，", "嗯，", "我在，慢慢说——", "我听着呢，", "好，我记下了——"],
    "low": ["嗯……我现在情绪有点低，不过你说吧——", "我这边有点闷闷的，",
            "我这会儿静得很，", "说真的我现在心里有点沉，"],
}
_ACK = ["你刚说「{h}」", "我把你这句「{h}」收下了", "「{h}」这句我记下了",
        "你说到「{h}」，我记住了"]
_FAMILIAR = ["这个「{t}」你以前也跟我提过，我们聊得不算少",
             "「{t}」这个话题我很熟了——你反复说过它",
             "又是「{t}」啊，我对它已经有印象了"]
_NOVEL = ["「{t}」这个说法我头一回听到，有点新鲜",
          "「{t}」对我来说是全新的，我想多知道一点",
          "这个「{t}」我还没见过，你多说点我就记住了"]
_ASSOC = ["跟「{t}」挨得近的，我还记得这些：{e}",
          "顺着「{t}」我想起来：{e}",
          "「{t}」让我串起了这几件事：{e}"]
_PREDICT = ["我隐约觉得你接下来大概会说到「{p}」",
            "我有种预感，你下一个想聊的可能是「{p}」",
            "按我学到的规律，接下来是「{p}」"]
_EPISODE = ["我记得，{e}", "翻我自己的经历——{e}", "之前有那么一次，{e}"]
_GUT = ["说不上来为什么，我心里有点{g}", "我的直觉在说{g}",
        "有种说不清楚的{g}感"]
_SLOW = ["这个我得慢慢想——我脑子里的第二套系统已经启动了",
         "别急，这事儿我用慢思在过一遍",
         "这事有点出乎我意料，我正在慢慢琢磨"]
_UNSURE = ["这个我真答不上来——我没学过，不想编给你听",
           "你说的这个我确实不知道，我不想瞎猜",
           "老实说我拿不准，与其编不如说不知道"]
_ASK = ["你呢，你怎么想的？", "你愿意再多说点吗？", "然后呢？我这会儿听得很认真",
        "这事儿你怎么看？", "要不要跟我细讲讲？", "你还有别的想说的吗？",
        "我记着呢，你接着说吧", "多跟我说说，我在。"]


class Pasm2Voice:
    """V2 感知 + 自然表达（无 LLM 档）。

    - `perceive(text)`：喂一句用户话进引擎（感知 + 外部指称），返回 digest。
    - `digest(text=None)`：读当前认知状态（真值；没有的字段 None）。
    - `compose(text, ...)`：状态驱动的自然回复（无 LLM）。
    - `brief()`：给 LLM 用的认知简报（只含真值）。
    """

    def __init__(self, kit: Any, persona: str = "温和沉稳", name: str = "小U",
                 encoder: Optional[TextEncoder] = None,
                 rng: Optional[random.Random] = None) -> None:
        self.kit = kit
        self.persona = persona
        self.name = name
        self.enc = encoder or TextEncoder()
        self._rng = rng or random.Random()
        self._used: Dict[str, List[int]] = {}      # 池名 → 最近用过的下标（去重）
        self._last_text = ""
        self._user_turns = 0
        self._terms: List[str] = []               # 引擎里见过的词（白名单）
        self._entity_hist: List[int] = []         # 最近象量 id（预测的上下文）
        self._last_ds: Optional[Dict[str, Any]] = None   # 最近一次门控状态

    # ------------------------------------------------------------ 感知
    @property
    def _mind(self) -> Any:
        return getattr(self.kit, "_mind", None)

    def perceive(self, text: str, interoception: Optional[Dict[str, float]] = None,
                 anomaly: Optional[bool] = None) -> Dict[str, Any]:
        """把一句用户话喂进引擎：① 象量感知 ② 外部指称绑定。"""
        t = (text or "").strip()
        if not t:
            return {}
        z = self.enc.encode(t)
        try:
            d = self.kit.observe(z, interoception=interoception, anomaly=anomaly)
        except Exception:                      # noqa: BLE001 —— 感知失败不影响回复
            return {}
        self._last_text = t
        self._user_turns += 1
        self.enc.count_terms(t)
        # 记录象量历史（预测器的上下文）与双系统门控状态（慢思真值）
        rep = getattr(self.kit, "_last_report", None) or {}
        ent = (rep.get("entity") or {}).get("id")
        if isinstance(ent, int):
            self._entity_hist.append(ent)
            self._entity_hist = self._entity_hist[-8:]
        if isinstance(rep.get("dual_system"), dict):
            self._last_ds = rep["dual_system"]
        # 外部指称：把这句话里的可读词挂到当前象量（符号化升级后经 SLH 成映射）
        try:
            for kw in self.enc.keywords(t, 3):
                self.kit.tell(kw)
                if kw not in self._terms:
                    self._terms.append(kw)
        except Exception:                      # noqa: BLE001
            pass
        self._terms = self._terms[-400:]
        return d

    def consolidate(self) -> Dict[str, Any]:
        """睡眠巩固（桌面此前从不调用）：让符号化/记忆图/DMN 真正跑起来。"""
        try:
            return self.kit.night()
        except Exception:                      # noqa: BLE001
            return {}

    # ------------------------------------------------------------ 状态读取
    def known_terms(self) -> List[str]:
        """引擎里**真见过**的词（表达层引用白名单，防编造）。"""
        return list(self._terms)

    def _neighbors(self, k: int = 3) -> List[Tuple[str, float]]:
        """记忆图里和"当前象量"关联最强的邻居（转成可读词）。"""
        mg = getattr(self._mind, "memory_graph", None)
        reg = getattr(self._mind, "registry", None)
        ent = getattr(self.kit, "_last_entity", None)
        if mg is None or reg is None or ent is None:
            return []
        try:
            pairs = mg.top_edges(int(ent), reg, max(k * 3, k))
        except Exception:                      # noqa: BLE001
            return []
        out: List[Tuple[str, float]] = []
        for eid, w in pairs:
            lab = self._label(eid)
            if self.readable(lab):                     # 只留能说人话的
                out.append((lab, float(w)))
        # 真词优先（跨轮词频高的才是用户真在说的）；片段排后；标签去重
        out.sort(key=lambda x: (-self.enc.freq(x[0]), -x[1]))
        labs = self.enc.prefer_real([lab for lab, _w in out])[:k]
        wmap = dict(out)
        return [(lab, wmap.get(lab, 0.0)) for lab in labs]

    @staticmethod
    def readable(label: str) -> bool:
        """标签是否"能说人话"：`S0` 这种内部编号不算（宁缺勿假）。"""
        s = (label or "").strip()
        if not s:
            return False
        if len(s) > 1 and s[0] in "ScseE" and s[1:].isdigit():
            return False
        return True

    def _topic_known(self, topic: str) -> bool:
        """这个话题词是不是"我们早就在聊的东西"（决定"熟"还是"生"）。

        ⚠️ 别用「本句象量是否新建」判断熟生（2026-09-29 踩到自相矛盾：
        同一句里既说「苹果我还没见过」，又说「跟苹果挨得近的我都记得」——
        因为这一**句**的新象量与「苹果」这个**概念**是两回事）。
        """
        if not topic:
            return False
        if self.enc.freq(topic) >= 2:                 # 用户反复说过
            return True
        sym = getattr(self._mind, "symbolizer", None)
        reg = getattr(self._mind, "registry", None)
        refs = getattr(sym, "_references", None) if sym is not None else None
        if isinstance(refs, dict) and reg is not None:
            for eid, words in refs.items():
                if topic in words:
                    node = reg.nodes.get(int(eid))
                    if node is not None and node.sample_count >= 2:
                        return True
        return False

    def _label(self, entity_id: int) -> str:
        """给象量起一个人话名字：优先它绑过的外部指称，其次 S<id>。"""
        sym = getattr(self._mind, "symbolizer", None)
        if sym is not None:
            refs = getattr(sym, "_references", None)
            if isinstance(refs, dict):
                got = refs.get(int(entity_id))
                if got:
                    return sorted(got, key=len, reverse=True)[0]
        return "S%d" % int(entity_id)

    def _episode(self, mood: Optional[Dict[str, float]]) -> Optional[str]:
        """自传体记忆里和当前情绪/实体最贴近的一段真实经历。"""
        am = getattr(self._mind, "autobiographical", None)
        if am is None:
            return None
        ent = getattr(self.kit, "_last_entity", None)
        try:
            r = am.cue(entity_ids=[ent] if ent is not None else None,
                       mood=mood, k=1)
        except Exception:                      # noqa: BLE001
            return None
        eps = (r or {}).get("episodes") or []
        if not eps:
            return None
        ep = eps[0]
        names = [self._label(e) for e in (ep.get("entities") or [])[:4]]
        names = [n for n in names if self.readable(n)]
        if not names:
            return None
        uniq = self.enc.prefer_real(names)
        v = float(ep.get("valence", 0.0) or 0.0)
        mood_word = "我心里是暖的" if v > 0.2 else ("我心里有点低" if v < -0.2
                                                   else "我心里是平的")
        return "聊到「%s」的那次，%s" % ("、".join(uniq[:2]), mood_word)

    def digest(self, text: Optional[str] = None) -> Dict[str, Any]:
        """当前认知状态（**真值**；引擎没有的一律 None）。"""
        st: Dict[str, Any] = {k: None for k in (
            "mood", "valence", "arousal", "gut", "novelty", "sim",
            "prediction", "prediction_conf", "associations", "episode",
            "symbols", "system2", "surprise", "topic", "familiar")}
        mind = self._mind
        if mind is None:
            return st
        rep = getattr(self.kit, "_last_report", None) or {}
        emo = (rep.get("emotion") or {})
        val = float(emo.get("valence", 0.0) or 0.0)
        aro = float(emo.get("arousal", 0.0) or 0.0)
        st.update(valence=round(val, 4), arousal=round(aro, 4),
                  mood=_band(val, aro))
        if "insula" in rep:
            st["gut"] = rep["insula"].get("somatic_marker")
        ent = (rep.get("entity") or {})
        if ent:
            st["novelty"] = ent.get("novelty")
            st["sim"] = ent.get("sim")
        if text:
            st["topic"] = self.enc.topic(text)
            # 「熟 / 生」以**话题概念**为准，不以此句象量是否新建为准
            st["familiar"] = self._topic_known(st["topic"])
        # 预测的可读化：拿预测的象量 → 它的名字
        pred = self._predict_next()
        if pred is not None:
            st["prediction"], st["prediction_conf"] = pred
        topic_w = st.get("topic") or ""
        assoc = [t for t, _ in self._neighbors(4) if t != topic_w]
        st["associations"] = assoc[:3] or None
        st["episode"] = self._episode({"valence": val, "arousal": aro})
        if mind.symbol_registry is not None:
            try:
                st["symbols"] = int(mind.symbol_registry.stats().get("n_symbols", 0))
            except Exception:                  # noqa: BLE001
                pass
        ds = getattr(mind, "dual_system", None)
        if ds is not None:
            last = getattr(self, "_last_ds", None)
            if isinstance(last, dict):
                st["system2"] = bool(last.get("system2"))
                st["surprise"] = last.get("surprise")
        if text:
            st["topic"] = self.enc.topic(text)
        return st

    def _predict_next(self) -> Optional[Tuple[str, float]]:
        """预测"下一个话题"（可读化）。证据不足 → None（不硬猜）。"""
        mind = self._mind
        pr = getattr(mind, "predictor", None)
        if pr is None:
            return None
        # 用最近若干步的象量 id 作为上下文（记忆图/预测器同源）
        hist = getattr(self, "_entity_hist", None)
        if not hist:
            return None
        try:
            got = pr.predict(list(hist))
        except Exception:                      # noqa: BLE001
            return None
        if not got:
            return None
        lab = self._label(int(got["entity"]))
        if not self.readable(lab):                 # 预测到的是"没名字的概念" → 不说
            return None
        return lab, float(got.get("confidence") or 0.0)

    # ------------------------------------------------------------ 表达
    def _pick(self, pool_name: str, pool: Sequence[str]) -> str:
        """加权抽样 + 近期去重（同一句式不连着用）。"""
        if not pool:
            return ""
        used = self._used.setdefault(pool_name, [])
        cand = [i for i in range(len(pool)) if i not in used]
        if not cand:
            used.clear()
            cand = list(range(len(pool)))
        i = self._rng.choice(cand)
        used.append(i)
        if len(used) > max(1, len(pool) // 2):
            used.pop(0)
        return pool[i]

    def _maybe(self, p: float) -> bool:
        return self._rng.random() < p

    def compose(self, text: str,
                notes: Optional[List[Dict[str, Any]]] = None) -> str:
        """**无 LLM** 的自然回复：状态决定说什么、用什么语气。

        结构：语气开口（可选） + 听觉回执（可选） + 1~2 条真证据主体（可选）
              + 收束（追问，可选）。所有槽位只填引擎真值；证据不足时如实说不知道。
        """
        t = (text or "").strip()
        d = self.digest(t)
        topic = d.get("topic") or ""
        heard = self._short(t)
        segs: List[str] = []

        # 1) 语气开口（按情绪档；低情绪档有时干脆不开口，避免"每次都同一句"）
        if self._maybe(0.72):
            segs.append(self._pick("open_" + d["mood"], _OPEN[d["mood"]]))

        # 2) 听觉回执（真·用户自己的话）
        if heard and self._maybe(0.6):
            segs.append(self._pick("ack", _ACK).format(h=heard))

        # 3) 主体：按证据强度排序，最多取 2 条
        bodies: List[str] = []
        if topic and d.get("familiar"):
            bodies.append(("familiar",
                           self._pick("familiar", _FAMILIAR).format(t=topic)))
        elif topic and float(d.get("novelty") or 1.0) > 0.5:
            bodies.append(("novel", self._pick("novel", _NOVEL).format(t=topic)))
        if d.get("associations"):
            assoc = "、".join(d["associations"][:3])
            bodies.append(("assoc", self._pick("assoc", _ASSOC).format(
                t=topic or "这件事", e=assoc)))
        if d.get("episode"):
            bodies.append(("episode",
                           self._pick("episode", _EPISODE).format(e=d["episode"])))
        if d.get("prediction"):
            bodies.append(("predict", self._pick("predict", _PREDICT).format(
                p=d["prediction"])))
        if d.get("gut") is not None and abs(float(d["gut"])) > 0.25:
            g = "不安" if float(d["gut"]) < 0 else "踏实"
            bodies.append(("gut", self._pick("gut", _GUT).format(g=g)))
        if d.get("system2"):
            bodies.append(("slow", self._pick("slow", _SLOW)))

        # 诚实门：三角校验（引擎自己的四层校验）——没依据就不装懂
        unsure = False
        try:
            verdict = self.kit.ask({"claim": t, "topic": topic})
            unsure = (verdict or {}).get("pass") is False
        except Exception:                      # noqa: BLE001
            unsure = False
        if unsure:
            segs.append(self._pick("unsure", _UNSURE))
        else:
            self._rng.shuffle(bodies)
            for _, b in bodies[:2]:
                segs.append(b)

        # 4) 收束：追问（偶尔省略，别显得像客服）
        if self._maybe(0.8):
            segs.append(self._pick("ask", _ASK))

        out = self._join(segs)
        if not out:                            # 全都没料 → 诚实而温暖的一句
            out = "我在听。你多说一点，我这边就多一点东西可以记住。"
        return out

    #: 视为"已断句"的结尾符号（前一段以这些收尾时不再补句号）
    _CLOSED = "。！？~…，、：；——"

    @classmethod
    def _join(cls, segs: Sequence[str]) -> str:
        """把片段接成自然的一句话：前段没断句就补句号，末尾补句号。

        （2026-09-29 踩坑：初版直接 "".join() —— 结果读出"我记住了这个「苹果」
          我还没见过"这种两句黏一起的怪话。）
        """
        out = ""
        for s in segs:
            s = (s or "").strip()
            if not s:
                continue
            if out and out[-1] not in cls._CLOSED:
                out += "。"
            out += s
        out = out.strip()
        if out and out[-1] not in "。！？~…":
            out += "。"
        return out

    @staticmethod
    def _short(text: str, n: int = 16) -> str:
        s = re.sub(r"\s+", " ", (text or "").strip())
        cut = re.split(r"[，。！？,.!?;；]", s)[0] or s
        cut = cut.strip()
        return cut[:n] + ("…" if len(cut) > n else "")

    # ------------------------------------------------------------ 给 LLM 的简报
    def brief(self, text: Optional[str] = None, max_items: int = 4) -> str:
        """V2 认知简报（喂给 LLM 的 system，只含真值；无内容返回空串）。"""
        d = self.digest(text)
        lines: List[str] = []
        if d.get("valence") is not None:
            lines.append("此刻情绪：愉悦 %+.2f、唤醒 %.2f%s"
                         % (d["valence"], d["arousal"],
                            "（心里有点悬）" if (d.get("gut") or 0) < -0.25 else
                            "（心里踏实）" if (d.get("gut") or 0) > 0.25 else ""))
        if d.get("prediction"):
            lines.append("上下文预测：接下来很可能聊到「%s」（把握 %.0f%%）"
                         % (d["prediction"], 100 * (d.get("prediction_conf") or 0)))
        if d.get("associations"):
            lines.append("记忆图联想：%s" % "、".join(d["associations"][:3]))
        if d.get("episode"):
            lines.append("相关经历（自传体）：%s" % d["episode"])
        if d.get("symbols"):
            lines.append("已成型的概念（真符号）：%d 个" % d["symbols"])
        if d.get("system2"):
            lines.append("内部状态：慢思通路已启动（这一轮对我是意外的）")
        if not lines:
            return ""
        head = ("【认知引擎 v2 的真实状态（不是设定，是这台机器此刻的实际数值）】\n"
                "这些是 PASM v2 认知内核**刚刚算出来的**，尽量自然地用起来"
                "（别提“系统提示”“引擎”这类词，就当是你自己的感受和记忆）：\n")
        return head + "\n".join("- " + l for l in lines[:max_items])

    # ------------------------------------------------------------ 自检
    def selftest(self) -> str:
        from pasm2.config import PASM2Config
        from pasm2.skills import CognitiveKit
        from dataclasses import replace
        cfg = replace(PASM2Config(), **CONV_ENGINE_KW)
        kit = CognitiveKit(profile="brainwide", config=cfg)
        v = Pasm2Voice(kit, rng=random.Random(0))
        for txt in ("我喜欢吃苹果", "上班今天好累", "我家猫咪很可爱",
                    "苹果真的挺好吃"):
            v.perceive(txt)
        v.perceive("我又想起苹果了")
        d = v.digest("我又想起苹果了")
        assert d["valence"] is not None, d
        assert v.enc.topic("我喜欢吃苹果") == "苹果", v.enc.topic("我喜欢吃苹果")
        outs = {v.compose("我喜欢吃苹果") for _ in range(8)}
        assert len(outs) >= 4, "表达多样性不足: %d" % len(outs)
        for o in outs:
            assert o and o[-1] in "。！？~…", o
        v.consolidate()
        assert isinstance(v.brief("苹果"), str)
        return ("Pasm2Voice selftest OK: perceive/digest/compose(多样 %d)"
                % len(outs))
