"""桌面端引擎工厂 —— 统一经 `pasm.engine_api` 契约创建"大脑"。

改造前（v0.28.5 及以前）
------------------------
`pasm_companion.py` 自己写了一条 try/except 链去猜哪个引擎能用：

    try:
        from pasm.agent import PASMAgent          # 需要 torch
        from pasm.config import PASMConfig
        from pasm.envs import GridWorld
    except Exception:
        from pasm_light import PASMAgent, PASMConfig, GridWorld   # 桌面自带兜底

问题不在"能不能用"，而在**它把具体引擎写死在了调用方**：
新增/替换一个引擎，就得回来改这里，并且所有 `self.agent.xxx` 的调用
都隐含"必须长得像 PASMAgent"。

改造后（v0.28.6 起）
--------------------
桌面只认**接口**：

    self.agent = EF.make_engine(personality_seed=..., seed=...)

`make_engine()` 内部：
  1. 把桌面自带的零依赖轻量体注册成契约引擎 `pasm-desktop-light`；
  2. 交给 `engine_api.create_best()` 按 `("pasm", "pasm-desktop-light")`
     优先级择优创建，并拿到一份**降级报告**（用了谁、试过谁、缺哪些能力）；
  3. 万一 `engine_api` 本身不可用，才退回最朴素的直连（保命路径）。

于是"换引擎不改代码"在桌面端真正生效：换引擎 = 换注册表里的名字，
调用方一行不用动。
"""
from __future__ import annotations

import logging

try:                                     # 契约层零依赖，桌面包内必可用
    from pasm import engine_api as EA
except Exception:                        # pragma: no cover - 极端兜底
    EA = None

#: 桌面自带轻量体在契约注册表里的名字
DESKTOP_LIGHT = "pasm-desktop-light"

#: 择优顺序：能跑完整七层引擎就用它，否则退到桌面轻量体
PREFERENCE = ("pasm", DESKTOP_LIGHT)

#: 轻量体诚实申报的能力（只有性格/情绪/发育三层）
LIGHT_CAPS = {"personality": True, "emotion": True, "development": True}

#: 最近一次引擎选择报告（给界面/自检读取）
LAST_REPORT: dict = {}

_registered = False


# ---------------------------------------------------------------- 注册
def _light_info():
    return EA.EngineInfo(
        name=DESKTOP_LIGHT, version="", kind="light",
        description="桌面自带零依赖轻量认知体（pasm_light，无 torch 也可跑）",
        deps=())


def register_desktop_light() -> bool:
    """把桌面轻量体注册成契约引擎（幂等）。"""
    global _registered
    if EA is None or _registered:
        return _registered

    def _factory(seed: int = 0, plan_samples: int = 8, plan_iters: int = 1,
                 personality_seed=None, **_ignored):
        import pasm_light as L
        brain = L.PASMAgent(L.PASMConfig(seed=seed),
                            personality_seed=personality_seed)
        return EA.as_engine(brain, info=_light_info(),
                            capabilities=EA.Capabilities.of(**LIGHT_CAPS))

    EA.register(DESKTOP_LIGHT, _factory, info=_light_info(), replace=True)
    _registered = True
    return True


# ---------------------------------------------------------------- 创建
def _note(report: dict) -> None:
    LAST_REPORT.clear()
    LAST_REPORT.update(report or {})


def _log(report: dict) -> None:
    used = report.get("used")
    if not used:
        return
    if report.get("degraded"):
        gap = report.get("gap") or []
        logging.info("引擎降级运行：%s（相对 %s 缺：%s）",
                     used, report.get("gap_vs") or "完整引擎",
                     "、".join(gap) if gap else "无")
    else:
        logging.debug("引擎就绪：%s", used)


def make_engine(personality_seed=None, seed: int = 0,
                plan_samples: int = 8, plan_iters: int = 1):
    """创建符合契约的"大脑"。返回对象满足 `engine_api.Engine`。"""
    register_desktop_light()

    if EA is not None:
        try:
            eng, report = EA.create_best(
                PREFERENCE, seed=seed, plan_samples=plan_samples,
                plan_iters=plan_iters, personality_seed=personality_seed)
            _note(report)
            _log(report)
            return eng
        except Exception as ex:                       # noqa: BLE001
            logging.warning("engine_api 择优创建失败，回退直连实现：%s", ex)

    return _direct_fallback(personality_seed, seed, plan_samples, plan_iters)


def _direct_fallback(personality_seed, seed, plan_samples, plan_iters):
    """保命路径：契约层不可用时的最朴素创建（不完全等价于择优）。"""
    try:
        from pasm.agent import PASMAgent
        from pasm.config import PASMConfig
        brain = PASMAgent(PASMConfig(seed=seed, plan_samples=plan_samples,
                                     plan_iters=plan_iters),
                          personality_seed=personality_seed)
        _note({"used": "pasm", "degraded": False, "tried": [], "gap": [],
               "source": "direct"})
        return brain
    except Exception:                                 # noqa: BLE001
        from pasm_light import PASMAgent, PASMConfig
        brain = PASMAgent(PASMConfig(seed=seed),
                          personality_seed=personality_seed)
        _note({"used": DESKTOP_LIGHT, "degraded": True, "tried": [], "gap": [],
               "source": "direct"})
        logging.info("引擎降级运行：%s（直连路径）", DESKTOP_LIGHT)
        return brain


def make_env(seed: int = 0):
    """创建网格环境（完整引擎带 numpy 版，轻量体带纯 Python 版）。"""
    try:
        from pasm.envs import GridWorld
    except Exception:                                 # noqa: BLE001
        from pasm_light import GridWorld
    return GridWorld(seed=seed)


# ---------------------------------------------------------------- 自述
def full_available() -> bool:
    """本机是否具备完整七层引擎（torch 可用）。"""
    if EA is None:
        return False
    try:
        return "pasm" in EA.available()
    except Exception:                                 # noqa: BLE001
        return False


def summary() -> dict:
    """给界面/自检用：引擎可用性 + 最近一次选择结果。"""
    out = {"full_available": full_available(), "preference": list(PREFERENCE),
           "desktop_light": DESKTOP_LIGHT, "last": dict(LAST_REPORT)}
    if EA is not None:
        try:
            out["engines"] = EA.available()
            out["api"] = EA.API_VERSION
        except Exception:                             # noqa: BLE001
            pass
    return out


def selftest() -> bool:
    """自检：注册 + 择优创建 + 契约一致性 + 冒烟。"""
    ok = True

    def check(cond, msg):
        nonlocal ok
        if not cond:
            ok = False
            print("  x %s" % msg)
        else:
            print("  v %s" % msg)

    print("engine_factory selftest")
    check(EA is not None, "契约层 pasm.engine_api 可用")
    register_desktop_light()
    if EA is not None:
        check(DESKTOP_LIGHT in EA.available(),
              "桌面轻量体已注册为契约引擎：%s" % DESKTOP_LIGHT)

    eng = make_engine(personality_seed=[0.8, -0.2, 0.6], seed=7)
    check(eng is not None, "make_engine 返回引擎")
    if EA is not None:
        good, probs = EA.conforms(eng, strict=True)
        check(good, "创建的引擎通过契约校验（问题：%s）" % (probs or "无"))
    eng.reset_episode()
    try:
        a, rep = eng.act([0.0] * 27)
        check(isinstance(rep, dict), "act() 返回 (动作, report)")
    except Exception as ex:                           # noqa: BLE001
        check(False, "act() 可用：%s" % ex)
    snap = eng.snapshot()
    check(isinstance(snap, dict) and "emotion" in snap, "snapshot 含 emotion 区块")

    env = make_env(seed=1)
    check(hasattr(env, "step") and hasattr(env, "reset"), "make_env 返回可用环境")

    info = summary()
    check(isinstance(info.get("last"), dict), "summary 可读取最近选择结果")
    print("engine_factory selftest:", "通过" if ok else "失败")
    return ok


if __name__ == "__main__":
    raise SystemExit(0 if selftest() else 1)
