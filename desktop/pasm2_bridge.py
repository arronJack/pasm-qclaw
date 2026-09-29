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
        extra={"surface": "api 2.0-surface", "engine": "pasm2"},
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

    def __init__(self, kit: Any) -> None:
        self._kit = kit
        #: 最近一次感知步得到的情绪（v2 真值）；没跑过就是 None（诚实缺省）
        self._last_emotion: Optional[dict] = None

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
def _kit():
    from pasm2.skills import CognitiveKit
    return CognitiveKit(profile="brainwide")


def make_v2_engine():
    """创建 v2 契约引擎（注册表路径）；不可用返回 None 并如实记日志。"""
    global _registered
    if EA is None:
        return None
    try:
        kit = _kit()
    except Exception as ex:             # noqa: BLE001
        logging.info("pasm2 引擎不可用（实验开关保持关闭）：%s", ex)
        return None

    def _factory(**_ignored):
        return EA.as_engine(Pasm2EngineAdapter(_kit()),
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


def maybe_make_engine():
    """开关开且 pasm2 可用 → 返回 v2 引擎；否则 None（调用方走原路径）。"""
    if not enabled() or EA is None:
        return None
    return make_v2_engine()
