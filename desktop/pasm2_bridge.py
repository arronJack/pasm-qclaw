"""pasm2_bridge —— 桌面端 PASM v2.0 引擎（实验性）单向桥。

定位（2026-09-29，零侵入纪律）
------------------------------
桌面端引擎全部经 `pasm.engine_api` 契约注册表择优创建（engine_factory.py）。
本模块把 **pasm2（v2.0 独立认知底座）** 包装成同一契约的引擎，供"实验开关"
选通——**默认关闭**：不设 `PASM2=1` 环境变量（或 UI 设置未开）时，
`engine_factory.make_engine` 的行为与历史版本逐字节一致。

设计约束（与 pasm2 侧铁律一致）：
  · 本模块是**桌面 → pasm2 的单向桥**，只 import pasm2 公开面
    （skills.CognitiveKit / capabilities），绝不 import `pasm.cognitive`
    内部实现（fork 铁律：防 desktop/ 与 pasm/cognitive 重复）；
  · pasm2 未安装 / 构造失败 → 如实回退（调用方继续走原择优路径），
    绝不让实验开关影响主链路稳定性；
  · 能力表诚实申报：v2 有的层才标 True，没有的层 False（不虚构）。
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

try:                                     # 契约层零依赖，桌面包内必可用
    from pasm import engine_api as EA
except Exception:                        # pragma: no cover
    EA = None

#: 表达/感知层（同目录模块；桌面包内 import 失败不影响引擎主链路）
try:
    from pasm2_voice import Pasm2Voice, CONV_ENGINE_KW
    _VOICE_OK = True
except Exception:                        # pragma: no cover
    try:
        import os as _os
        import sys as _sys
        _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
        from pasm2_voice import Pasm2Voice, CONV_ENGINE_KW
        _VOICE_OK = True
    except Exception:
        Pasm2Voice = None                # type: ignore
        CONV_ENGINE_KW = {}
        _VOICE_OK = False

#: 实验开关的环境变量名（UI 设置项落地前先用 env 灰度）
ENV_SWITCH = "PASM2"

#: 契约注册表里的 v2 引擎名
PASM2_ENGINE = "pasm2"

_registered = False


# ---------------------------------------------------------------- 能力申报
def _v2_capabilities():
    """pasm2 brainwide 档的诚实能力表（对照七层架构）。"""
    return EA.Capabilities.of(
        perception=True,        # 象量软分配感知
        memory=True,            # 实体记忆/记忆图/自传体
        emotion=True,           # 四通道调质 + 岛叶内感受
        metacognition=True,     # ACC + 双系统门控
        symbolic=True,          # 符号化 + SLH 桥 + 三角校验
        world_model=True,       # 反事实推理（预测器前向推演）
        # 诚实缺位：working_memory 层面 v2 有但语义不同（前瞻记忆），
        # 桌面契约里该位留给 V1 七层语义，故不标——宁缺勿假。
        personality=False,      # 性格先验是 V1 层，v2 未实装同语义
        development=False,      # 发育可塑性（阶段 10 后续）
        global_workspace=False,
        llm=False, multimodal=False, persistence=False,
        extra={"surface": "api 2.0-surface", "engine": "pasm2",
               # v0.31.13：v2 现在也带"对话感知 + 无 LLM 自然表达"（状态驱动，非预设答句）
               "dialogue_perception": _VOICE_OK,
               "natural_expression": _VOICE_OK,
               "expression_kind": "state_driven" if _VOICE_OK else None},
    )


def _v2_info():
    return EA.EngineInfo(
        name=PASM2_ENGINE, version=_v2_version(), kind="cognitive",
        description="PASM v2.0 类脑认知底座（实验性；预测/记忆/符号/双系统，numpy-only）",
        deps=("numpy",))


def _v2_version() -> str:
    try:
        import pasm2
        return pasm2.__version__
    except Exception:
        return ""


# ---------------------------------------------------------------- 适配器
class Pasm2EngineAdapter:
    """把 CognitiveKit 适配成 `engine_api.Engine` 结构化协议。

    语义映射（诚实，宁缺勿假）：
      · act(obs)      → obs 为向量时执行感知步；obs 为 {"candidates": [...]}
                        字典时走量子叠加决策选一个候选；其余返回 None。
      · learn(...)    → 观察回报进情绪（效价），返回 v2 的 digest。
      · snapshot()    → kit.status() 精选状态 + 阶段标记。
      · reset_episode() → 无 episode 语义，no-op（如实）。
    """

    def __init__(self, kit: Any, persona: str = "温和沉稳", name: str = "小U") -> None:
        self._kit = kit
        #: 最近一次感知步得到的情绪（v2 真值）；没跑过就是 None（诚实缺省）
        self._last_emotion: Optional[dict] = None
        #: 对话感知 + 自然表达层（v0.31.13；不可用时为 None，回复退回原路径）
        self.voice = (Pasm2Voice(kit, persona=persona, name=name)
                      if _VOICE_OK and Pasm2Voice is not None else None)

    # ---- 对话感知 / 自然表达（桌面专用扩展，非 Engine 契约） ----
    def perceive_text(self, text: str,
                      interoception: Optional[dict] = None) -> dict:
        """把用户这一句喂进认知引擎（象量感知 + 外部指称），返回 digest。

        桌面此前只喂 GridWorld 的随机观测，用户原话从没进过引擎 —— 于是
        V2 的"上下文预测"在真实对话里无料可预测。这个入口补上这一环。
        """
        if self.voice is None:
            return {}
        return self.voice.perceive(text, interoception=interoception)

    def cognition_brief(self, text: Optional[str] = None) -> str:
        """给 LLM 的 V2 认知简报（真值；无可报内容返回空串）。"""
        return self.voice.brief(text) if self.voice is not None else ""

    def compose_reply(self, text: str, notes: Optional[list] = None) -> str:
        """**无 LLM** 的自然回复（状态驱动，非关键词答句表）。"""
        if self.voice is None:
            return ""
        return self.voice.compose(text, notes=notes)

    def consolidate(self) -> dict:
        """睡眠巩固（桌面此前从不调用 → 符号化/记忆图/DMN 永不运行）。"""
        return self.voice.consolidate() if self.voice is not None else {}

    # ---- Engine 契约 ----
    def info(self) -> "EA.EngineInfo":
        return _v2_info()

    def capabilities(self) -> "EA.Capabilities":
        return _v2_capabilities()

    def reset_episode(self) -> Any:
        return None                     # v2 无 episode 语义（诚实 no-op）

    def act(self, obs: Any, learning: bool = True) -> Any:
        if isinstance(obs, dict) and obs.get("candidates"):
            try:
                return self._kit.decide(obs["candidates"])
            except Exception:           # noqa: BLE001 —— 决策失败诚实返回 None
                return None
        z = getattr(obs, "tolist", lambda: obs)()
        try:
            rep = self._kit.observe(z)
        except Exception:               # noqa: BLE001
            return None
        if isinstance(rep, dict):
            emo = rep.get("emotion")
            if isinstance(emo, dict):
                self._last_emotion = {
                    "valence": float(emo.get("valence", 0.0) or 0.0),
                    "arousal": float(emo.get("arousal", 0.0) or 0.0)}
            ent = rep.get("entity")
            return ent.get("id") if isinstance(ent, dict) else ent
        return None

    def learn(self, obs: Any, action: Any, next_obs: Any, reward: float,
              report: Optional[dict] = None) -> dict:
        """效价反馈进情绪系统（诚实：轻量语义，不做完整 RL）。"""
        try:
            emo = getattr(self._kit._mind, "emotion", None)
            if emo is not None:
                emo.update(valence=max(-1.0, min(1.0, float(reward))))
        except Exception:               # noqa: BLE001
            pass
        return {"ok": True, "engine": PASM2_ENGINE}

    def snapshot(self) -> dict:
        """契约快照（**诚实缺省**：v2 没有的区块给 None，不编造数值）。

        桌面 UI 会读 emotion / personality / development / memory 这几块；
        v2 真实有的是**情绪**（感知步的效价/唤醒）与**规模计数**，
        性格维度与成长阶段在 v2 里没有对应物 → 如实 None（UI 按"缺就少说"渲染）。
        """
        st = self._kit.status()
        try:
            import pasm2
            ver = pasm2.__version__
        except Exception:               # noqa: BLE001
            ver = ""
        running = {k: st.get(k) for k in (
            "step", "entities", "symbols", "prediction_hit_rate",
            "memory_graph_edges", "slh_one_to_one_ratio")
            if isinstance(st.get(k), (int, float, str, bool))}
        return {"engine": PASM2_ENGINE, "version": ver,
                "profile": st.get("profile")
                           or getattr(self._kit, "profile", "brainwide"),
                "running": running,
                "emotion": dict(self._last_emotion) if self._last_emotion else None,
                "personality": None, "development": None}


# ---------------------------------------------------------------- 注册/选通
def _kit(persona: str = "温和沉稳", name: str = "小U"):
    """创建对话用 kit：brainwide profile + **对话感知专用参数**（见 pasm2_voice）。

    posture：`CONV_ENGINE_KW` 只调 entity 潜维/聚类阈值与预测门槛，
    profile 的能力开关集合不变（brainwide 仍是 brainwide）；内核默认值也不动，
    因此既有验证链与其它 kit 使用者行为逐字节不变。
    """
    from dataclasses import replace
    from pasm2.config import PASM2Config
    from pasm2.skills import CognitiveKit
    base = replace(PASM2Config(), **CONV_ENGINE_KW) if CONV_ENGINE_KW \
        else PASM2Config()
    return CognitiveKit(profile="brainwide", config=base)


def make_v2_engine(persona: str = "温和沉稳", name: str = "小U"):
    """创建 v2 契约引擎（注册表路径）；不可用返回 None 并如实记日志。"""
    global _registered
    if EA is None:
        return None
    try:
        _kit(persona, name)
    except Exception as ex:             # noqa: BLE001
        logging.info("pasm2 引擎不可用（实验开关保持关闭）：%s", ex)
        return None

    def _factory(**_ignored):
        return EA.as_engine(Pasm2EngineAdapter(_kit(persona, name),
                                               persona=persona, name=name),
                            info=_v2_info(), capabilities=_v2_capabilities())

    try:
        EA.register(PASM2_ENGINE, _factory, info=_v2_info(), replace=True)
        _registered = True
        eng, report = EA.create_best((PASM2_ENGINE,), seed=0)
        return eng
    except Exception as ex:             # noqa: BLE001
        logging.warning("pasm2 引擎注册/创建失败：%s", ex)
        return None


def _config_flag() -> bool:
    """读本机 config.json 的 ``engine_v2``（「设置 → 模型与思考」里那个勾选框写的键）。

    只读、绝不写；读不到/JSON 坏/键缺失一律当 ``False``（不虚构开关状态）。
    数据目录走 `logsetup.data_dir()` 单一来源，避免与主程序漂移。
    """
    try:
        import logsetup
        data_dir = logsetup.data_dir()
    except Exception:                        # pragma: no cover
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        data_dir = os.path.join(base, "PASMStudio")
    try:
        with open(os.path.join(data_dir, "config.json"), encoding="utf-8") as f:
            return bool(json.load(f).get("engine_v2"))
    except Exception:
        return False


def switch_source() -> str:
    """开关来自哪里（供 UI / 日志诚实申报）：``"env"`` / ``"config"`` / ``""``。"""
    if os.environ.get(ENV_SWITCH, "").strip():
        return "env"
    return "config" if _config_flag() else ""


def enabled() -> bool:
    """实验开关是否打开。

    优先级：环境变量 ``PASM2``（开发/排障用，``0/false`` 可强制关掉）→
    ``config.json`` 的 ``engine_v2``（设置界面勾选，面向用户）。
    """
    v = os.environ.get(ENV_SWITCH, "").strip().lower()
    if v:
        return v in ("1", "true", "yes", "on")
    return _config_flag()


def maybe_make_engine(persona: str = "温和沉稳", name: str = "小U"):
    """开关开且 pasm2 可用 → 返回 v2 引擎；否则 None（调用方走原路径）。"""
    if not enabled() or EA is None:
        return None
    return make_v2_engine(persona=persona, name=name)
