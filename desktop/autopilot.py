# -*- coding: utf-8 -*-
"""autopilot.py —— 自主 Tick 心跳（v0.30.5）

对标白龙马（BaiLongma）`main.cjs` 的常驻主循环：空闲时不只驱动动画，
还做「**崩溃恢复 + 续跑巡检 + 状态推送 + 数据体检**」。

## 安全边界（本模块最重要的部分，改之前先读）

1. **绝不假装执行**：本模块**永不**把任何一步标成 `done`。
   进度数字只能来自真实完成（`worklog.step_complete`）。
   早期设计想过"心跳自动把下一步标成 running"——那就是撒谎，
   因为并没有真的在执行，用户会看到一个永远绿着的假进度。
2. **绝不删用户数据**：`consolidate()` **只统计、只报告**，
   不改动也不删除任何工作记录（`worklog` 自身有 200 条的写入上限，
   收敛是"不再增长"，不是"清理掉你的历史"）。
3. **只读巡检是默认**：`tick()` 即使 `enabled=True` 也只更新
   「下一步提示」这类**幂等的、可回退的**字段。
   真正执行一律走 `workflow_engine.run_step(tid, fn)` 且必须显式传执行器。
4. **失败即降级**：任何一节出错都只是那一节不产出，绝不抛到界面层。

## 节流

面板的定时器是 5s 一跳，但重活（扫描 + 写盘）没必要 5s 一次。
`MIN_GAP` 内的重复调用直接返回上次结果（`force=True` 可跳过）。
"""
from __future__ import annotations

import json
import os
import time
from typing import Dict, List, Optional

import worklog as WL

MIN_GAP = 20.0          # 秒：两次"重活"之间的最小间隔（面板 5s 一跳，故节流）
_STATUS_FILE = "autopilot.json"

# 运行期状态（不落盘，进程级）
_STATE = {"ticks": 0, "heavy": 0, "last_heavy": 0.0,
          "last_msg": "", "recovered": 0, "boot_done": False,
          "last": {}}


def _state_path() -> str:
    return os.path.join(WL._data_dir(), _STATUS_FILE)


def _load_pref() -> dict:
    try:
        with open(_state_path(), "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:                       # noqa: BLE001
        return {}


def _save_pref(d: dict):
    try:
        os.makedirs(WL._data_dir(), exist_ok=True)
        tmp = _state_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _state_path())
    except Exception:                       # noqa: BLE001
        pass


def is_enabled() -> bool:
    """自主续跑开关（默认**关** —— 未明确开启时只做只读巡检）。

    默认关的理由：用户没答应就自动改他的工作状态，比不自动更糟。
    """
    return bool(_load_pref().get("enabled", False))


def enable(on: bool = True):
    d = _load_pref()
    d["enabled"] = bool(on)
    d["changed"] = time.time()
    _save_pref(d)
    _STATE["last_msg"] = "自主续跑已%s" % ("开启" if on else "关闭")


# --------------------------------------------------------------------------
# 各节（都可单独调用）
# --------------------------------------------------------------------------
def recover() -> int:
    """崩溃恢复：把上次进程没跑完的 running 标成 interrupted（**只标不删**）。"""
    try:
        return int(WL.mark_running_as_interrupted() or 0)
    except Exception:                       # noqa: BLE001
        return 0


def scan_resumable(limit: int = 3) -> List[dict]:
    """巡检可续跑的工作流：返回 [{root_id, title, next_id, next_title, pct}]。

    **只读**：不改任何状态。
    """
    out = []
    try:
        import workflow_engine as WF
        for t in WL.recent(60):
            if (t.get("level") or 0) != 0 or t.get("kind") != "workflow":
                continue
            if t.get("status") == "done":
                continue
            nxt = WF.next_step(t.get("id"))
            if not nxt:
                continue
            p = WL.progress_of(t.get("id"))
            out.append({"root_id": t.get("id"), "title": (t.get("title") or "")[:24],
                        "next_id": nxt.get("id"),
                        "next_title": (nxt.get("title") or "")[:24],
                        "pct": p.get("pct", 0), "done": p.get("done", 0),
                        "total": p.get("total", 0)})
            if len(out) >= limit:
                break
    except Exception:                       # noqa: BLE001
        return []
    return out


def push_hints(items: Optional[List[dict]] = None) -> int:
    """把「下一步」写回父工作流（幂等、可回退；**不改变完成状态**）。

    这是 enabled=True 时唯一会写盘的动作，且写的只是提示文本。
    """
    if not is_enabled():
        return 0
    items = scan_resumable() if items is None else items
    n = 0
    for it in items:
        try:
            want = "续跑：%s（%d/%d 步已完成）" % (
                it["next_title"] or "下一步", it["done"], it["total"])
            cur = (WL.get(it["root_id"]) or {}).get("next_step") or ""
            if cur != want:
                WL.set_next(it["root_id"], want)
                n += 1
        except Exception:                   # noqa: BLE001
            continue
    return n


def consolidate() -> dict:
    """数据体检：**只统计、不删除**（见模块头安全边界 2）。"""
    info = {"tasks": 0, "unfinished": 0, "workflows": 0, "bytes": 0}
    try:
        info["tasks"] = len(WL.recent(500))
        info["unfinished"] = len(WL.unfinished())
        info["workflows"] = sum(1 for t in WL.recent(200)
                                if t.get("kind") == "workflow")
        p = getattr(WL, "_path", None)
        if p:
            try:
                info["bytes"] = os.path.getsize(p())
            except Exception:               # noqa: BLE001
                pass
    except Exception:                       # noqa: BLE001
        pass
    return info


def status() -> dict:
    """后台链路状态（给「查看状态」用；不执行任何动作）。"""
    st = dict(_STATE)
    st["enabled"] = is_enabled()
    st["data"] = consolidate()
    st["resumable"] = scan_resumable()
    return st


# --------------------------------------------------------------------------
# 主入口：一次 Tick
# --------------------------------------------------------------------------
def tick(force: bool = False, ctx: Optional[dict] = None) -> dict:
    """一次自主巡检。返回 {ran, actions, hint, resumable, data}。

    节流：MIN_GAP 内的重复调用直接返回上次结果（除非 force）。
    """
    now = time.time()
    _STATE["ticks"] += 1
    if not force and _STATE["last_heavy"] and (now - _STATE["last_heavy"]) < MIN_GAP:
        last = dict(_STATE["last"])
        last["ran"] = False
        return last

    actions = []

    # ① 开机首次：崩溃恢复（只标不删）
    if not _STATE["boot_done"]:
        _STATE["boot_done"] = True
        n = recover()
        _STATE["recovered"] = n
        if n:
            actions.append("恢复 %d 条中断的工作（标记为 interrupted，未删除）" % n)

    # ② 巡检可续跑（只读）
    items = scan_resumable()
    if items:
        actions.append("发现 %d 个可续跑的工作流" % len(items))

    # ③ 推送下一步提示（仅在显式开启时写盘；写的是提示文本，可回退）
    pushed = push_hints(items)
    if pushed:
        actions.append("更新 %d 条「下一步」提示" % pushed)

    # ④ 数据体检（只统计）
    data = consolidate()

    hint = "｜".join(actions) if actions else "一切正常，没有待续跑的工作"
    if items:
        it = items[0]
        hint += "。最近一个：%s（%d/%d，下一步 %s）" % (
            it["title"], it["done"], it["total"], it["next_title"])

    out = {"ran": True, "actions": actions, "hint": hint, "resumable": items,
           "data": data, "enabled": is_enabled(), "at": now}
    _STATE["last_heavy"] = now
    _STATE["heavy"] += 1
    _STATE["last_msg"] = hint
    _STATE["last"] = out
    return out


def reset_runtime():
    """清空进程级运行状态（测试用；不动任何落盘数据）。"""
    _STATE.update({"ticks": 0, "heavy": 0, "last_heavy": 0.0, "last_msg": "",
                   "recovered": 0, "boot_done": False, "last": {}})


# --------------------------------------------------------------------------
# 自检（隔离数据目录；重点验"不该做的事一件都没做"）
# --------------------------------------------------------------------------
def selftest() -> int:
    import shutil
    import tempfile

    passed = failed = 0

    def check(name, cond, extra=""):
        nonlocal passed, failed
        if cond:
            passed += 1
            print("  ok   %s" % name)
        else:
            failed += 1
            print("  FAIL %s %s" % (name, extra))

    tmp = tempfile.mkdtemp(prefix="pasm_auto_")
    old = os.environ.get("PASM_STUDIO_DIR")
    os.environ["PASM_STUDIO_DIR"] = tmp
    try:
        import workflow_engine as WF
        reset_runtime()

        # ---------- 默认关闭（不自动改用户状态） ----------
        check("默认未开启", is_enabled() is False)

        # ---------- 空台账：tick 不炸 ----------
        r = tick(force=True)
        check("空台账 tick 不炸", r["ran"] is True and isinstance(r["hint"], str))
        check("空台账无可续跑", r["resumable"] == [])
        check("空台账数据体检可用", r["data"]["tasks"] == 0)

        # ---------- 崩溃恢复：只标 interrupted，不删除 ----------
        t1 = WL.create("上次没跑完的活", "general", dir="d")
        WL.set_status(t1["id"], "running")
        reset_runtime()
        r = tick(force=True)
        check("首次 tick 做崩溃恢复", r["data"]["tasks"] == 1, str(r["data"]))
        check("任务没被删除", WL.get(t1["id"]) is not None)
        check("恢复不改完成状态",
              (WL.get(t1["id"]) or {}).get("status") != "done",
              str((WL.get(t1["id"]) or {}).get("status")))

        # ---------- 续跑巡检：只读，未开启时不写盘 ----------
        rid = WF.plan_workflow("做一个招生海报并写文案", dir="营销")
        before = (WL.get(rid) or {}).get("next_step") or ""
        reset_runtime()
        r = tick(force=True)
        check("发现可续跑工作流", len(r["resumable"]) >= 1, str(r["resumable"]))
        check("未开启时不写回 next_step",
              ((WL.get(rid) or {}).get("next_step") or "") == before,
              "%r -> %r" % (before, (WL.get(rid) or {}).get("next_step")))

        # ---------- ★ 核心安全断言：tick 永不把步骤标成 done ----------
        kids = WL.children_of(rid)
        check("工作流有子步骤", len(kids) >= 1, str(len(kids)))
        for _ in range(3):
            tick(force=True)
        done_after = [k for k in WL.children_of(rid) if k.get("status") == "done"]
        check("★ tick 不会把任何步骤标成 done（不假装执行）",
              len(done_after) == 0, "完成数=%d" % len(done_after))

        # ---------- 开启后：只更新提示文本，仍不改完成状态 ----------
        enable(True)
        check("开启后 is_enabled=True", is_enabled() is True)
        reset_runtime()
        r = tick(force=True)
        after = (WL.get(rid) or {}).get("next_step") or ""
        check("开启后写回「续跑」提示", "续跑" in after, repr(after))
        check("★ 开启后仍无步骤被标 done",
              len([k for k in WL.children_of(rid) if k.get("status") == "done"]) == 0)
        check("开写后父任务状态仍是未完成",
              (WL.get(rid) or {}).get("status") != "done")

        # ---------- 幂等：同一提示不重复写 ----------
        r2 = tick(force=True)
        # 上一次 tick 已把「续跑：…」写进去，这次内容没变就不该再写盘。
        # 断言直接看「有没有出现『更新 N 条』」，而不是绕一层字符串拼接
        # （第一版把断言写成 "[%s]" % a in "[%s]" % b，是句废话，永远为假）。
        check("重复 tick 幂等（不重复写）",
              not any("更新" in a for a in r2["actions"]), str(r2["actions"]))

        # ---------- 节流 ----------
        reset_runtime()
        a = tick(force=True)
        b = tick(force=False)
        check("节流生效（20s 内不重复干重活）", b.get("ran") is False, str(b.get("ran")))
        c = tick(force=True)
        check("force 可跳过节流", c.get("ran") is True)

        # ---------- consolidate 只统计不删除 ----------
        n_before = len(WL.recent(500))
        info = consolidate()
        check("体检只统计", info["tasks"] == n_before, str(info))
        check("★ 体检后任务一条没少", len(WL.recent(500)) == n_before)
        check("体检含未完成数", "unfinished" in info)

        # ---------- status() 不抛 ----------
        st = status()
        check("status 可读开关", st["enabled"] is True)
        check("status 含数据与可续跑", "data" in st and "resumable" in st)
        check("status 不改变任务数", len(WL.recent(500)) == n_before)

        # ---------- 状态文件损坏 → 视为关闭，不炸 ----------
        with open(os.path.join(tmp, _STATUS_FILE), "w", encoding="utf-8") as f:
            f.write("{坏掉的 json")
        check("状态文件损坏 → 视为关闭", is_enabled() is False)
        enable(True)
        check("损坏后可重新写入开关", is_enabled() is True)
        check("损坏不牵连工作台账", len(WL.recent(500)) == n_before)
    finally:
        if old is None:
            os.environ.pop("PASM_STUDIO_DIR", None)
        else:
            os.environ["PASM_STUDIO_DIR"] = old
        shutil.rmtree(tmp, ignore_errors=True)

    print("-" * 46)
    print("autopilot 自检：%d 项，%s"
          % (passed + failed, "全部通过" if not failed else "%d 项失败" % failed))
    return 1 if failed else 0


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        raise SystemExit(selftest())
    print("自主 Tick 试跑：")
    print(tick(force=True)["hint"])
    print("=" * 46)
    selftest()
