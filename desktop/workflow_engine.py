# -*- coding: utf-8 -*-
"""workflow_engine.py —— AI 自动化工作流引擎（v0.30.4）

把「一个目标」拆成可执行的层级任务，并持续追踪进度与「下一步」：
  · plan_workflow(goal)  → 用 planner 拆步骤，建父任务(level0) + 子任务(level1, parent_id)
  · next_step(root_id)   → 下一个未完成的子任务（面板「下一步」用）
  · run_step(tid, fn)    → 执行某步（fn 是真实执行器，缺省只标记进行中）
  · summarize(root_id)   → 整体进度百分比
对接 worklog（聊天↔工作关联）与 planner（步骤拆解），离线可用（优先规则拆解）。
"""
from __future__ import annotations

import worklog as WL


def _steps_of(goal: str) -> list:
    """拆步骤：优先 planner.rule_steps，失败则退化为单步。"""
    try:
        import planner as PLN
        st = PLN.rule_steps(goal)
        if st:
            return st
    except Exception:
        pass
    return [{"title": (goal or "").strip()[:50], "detail": ""}]


def _kind_of(title: str) -> str:
    """推断一步属于哪个工种（交给能力注册表判，而不是硬写 "general"）。

    这是「声明式能力注册表」真正落地的地方：**工作流不自己维护工种清单**，
    新增工种只改 `capability.py` 一处。实测踩到过：这里原来一律写
    `kind="general"`，导致建出来的子步骤永远找不到执行器，
    `run_next` 只能一直报 `no_executor`（"注册表"白建了）。

    用放宽窗口（WINDOW_DISTILLED）：步骤标题已经是提炼过的短指令，
    不像原始用户语句那样会在中段随口提到某个词。
    """
    try:
        import capability as CAP
        c = CAP.best(title, None, getattr(CAP, "WINDOW_DISTILLED", 40))
        return c.get("kind") or "general"
    except Exception:  # noqa: BLE001  注册表不可用也不能影响建工作流
        return "general"


def plan_workflow(goal: str, dir: str = "", llm_fn=None) -> str:
    """为目标建立层级工作流，返回父任务 id。"""
    goal = (goal or "").strip()
    if not goal:
        return ""
    steps = _steps_of(goal)
    root = WL.create(title=goal[:50], kind="workflow", dir=dir or "自动化工作",
                    level=0, next_step="规划完成，待启动首步")
    for i, s in enumerate(steps, 1):
        title = (s.get("title") or "").strip()[:50] or ("步骤%d" % i)
        # kind 由能力注册表推断（不是一律 general），否则找不到执行器
        WL.create(title=title, kind=_kind_of(title), dir=dir or "自动化工作", level=1,
                  parent_id=root["id"],
                  next_step="待执行：%s" % ((s.get("detail") or title)[:120]))
    WL.set_next(root["id"], "启动第 1 步：%s" % ((steps[0].get("title") or "")[:80]))
    return root["id"]


def next_step(root_id: str) -> dict:
    """下一个未完成的子任务（按创建顺序）。"""
    if not root_id:
        return {}
    for c in WL.children_of(root_id):
        if c.get("status") != "done":
            return c
    return {}


def run_step(tid: str, fn=None) -> dict:
    """执行某步：fn(step_dict) -> (ok, progress_text, artifacts)。无 fn 仅标记进行中。"""
    t = WL.get(tid)
    if not t:
        return {}
    if fn:
        try:
            ok, prog, arts = fn(t)
        except Exception as e:  # noqa: BLE001
            WL.step_complete(tid, "❌ 执行异常：%s" % e, ok=False)
            return t
        for a in (arts or []):
            WL.add_artifact(tid, a)
        WL.step_complete(tid, prog, ok=ok)
    else:
        WL.set_status(tid, "running", "🔧 进行中…")
    pid = (WL.get(tid) or {}).get("parent_id") or ""
    nxt = next_step(pid)
    if pid:
        WL.set_next(pid, ("下一步：%s" % (nxt.get("title") or "已完成"))[:120]
                    if nxt else "🎉 全部步骤完成")
    return t


def summarize(root_id: str) -> dict:
    return WL.progress_of(root_id)


# --------------------------------------------------------------------------
# v0.30.5：执行器注册表 + 真续跑（对标白龙马 task-manager）
# --------------------------------------------------------------------------
# 诚实边界（改之前先读）：
#   · **没有执行器就不许标完成**。否则进度条会一直绿着，而什么都没做。
#     找不到执行器时返回 ok=False + reason="no_executor"，由界面如实说明。
#   · 执行器抛异常 = 这一步失败（failed），不是"完成"。
EXECUTORS = {}          # kind -> fn(step, ctx) -> (ok, progress_text, artifacts)


def attach_executor(kind: str, fn):
    """注册某工种的真执行器；新增能力不必改本模块任何调度代码。"""
    EXECUTORS[kind] = fn
    return fn


def executor_for(kind: str):
    return EXECUTORS.get(kind) or EXECUTORS.get("*")


def install_defaults():
    """装内置执行器（离线可用、只产真实文件）。重复调用安全。"""
    try:
        import ad_design as AD
        attach_executor("ad", AD.executor)
    except Exception:  # noqa: BLE001  装不上就少一个执行器，不影响其它
        pass
    return dict(EXECUTORS)


def run_next(root_id: str, ctx: dict = None) -> dict:
    """执行工作流的「下一步」。返回 {ok, reason?, step?, progress?, artifacts?}。

    与 run_step 的区别：run_step 是**单个步骤**的入口；本函数负责
    「取下一步 → 找执行器 → 真跑 → 回写父进度」这条链。
    **没有执行器时只标记进行中，绝不标完成。**
    """
    ctx = ctx or {}
    step = next_step(root_id)
    if not step:
        return {"ok": True, "reason": "nothing_to_do",
                "progress": "🎉 全部步骤已完成"}
    fn = executor_for(step.get("kind") or "")
    if not fn:
        # ⚠️ 不假装执行：只把这一步标成"进行中"，进度维持原样
        WL.set_status(step["id"], "running", "🔧 进行中（该工种暂无自动执行器）")
        WL.set_next(step["id"], "该工种暂无自动执行器，请在对话里完成这一步")
        return {"ok": False, "reason": "no_executor", "step": step,
                "progress": "该工种暂无自动执行器，已标记进行中（未标完成）"}
    run_step(step["id"], fn=lambda s: fn(s, ctx))
    st = WL.get(step["id"]) or {}
    return {"ok": st.get("status") == "done", "step": step,
            "progress": st.get("progress") or "",
            "artifacts": st.get("artifacts") or [],
            "pct": WL.progress_of(root_id)["pct"]}


def resumable(limit: int = 3) -> list:
    """可续跑的工作流（只读巡检；面板与 autopilot 共用）。"""
    out = []
    for t in WL.recent(60):
        if (t.get("level") or 0) != 0 or t.get("kind") != "workflow":
            continue
        if t.get("status") == "done":
            continue
        nxt = next_step(t.get("id"))
        if not nxt:
            continue
        p = WL.progress_of(t.get("id"))
        out.append({"root_id": t.get("id"),
                    "title": (t.get("title") or "")[:24],
                    "next_id": nxt.get("id"),
                    "next_title": (nxt.get("title") or "")[:24],
                    "pct": p["pct"], "done": p["done"], "total": p["total"]})
        if len(out) >= limit:
            break
    return out


install_defaults()


if __name__ == "__main__":
    rid = plan_workflow("给霖云智学做一个开学季招生海报", dir="营销/海报")
    print("root:", rid)
    nxt = next_step(rid)
    print("first step:", nxt.get("title"))
    run_step(nxt["id"])
    print("progress:", summarize(rid))
    print("SELFTEST_OK")
