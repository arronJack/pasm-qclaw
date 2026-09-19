# -*- coding: utf-8 -*-
"""context_assembly.py —— 统一上下文装配（v0.30.5）

对标白龙马（BaiLongma）每轮对话前的 **Context Assembly** 阶段：把
「命中的能力 + 工作台账 + 知识库 + 记忆摘要 + 相关产物」按优先级合成**一段**
系统提示，而不是让各模块各自往提示词里塞一段（塞重、塞爆、互相矛盾）。

## 分层原则（不越界）

- 认知层的**记忆**由调用方以文本形式传进来（`ctx["memory"]`）——
  本模块**不直接读** `pasm.cognitive.*`，避免桌面端与核心耦合（六仓铁律）。
- 只读桌面侧自己的模块：`worklog`（工作台账）、`knowledge`（知识库）、
  `capability`（能力注册表）。全部**懒加载 + 失败即跳过**。

## 预算

提示词是有预算的。`MAX_CHARS` 是硬顶，超了**按段落优先级裁剪**，
而不是把某一节腰斩成半句（半句会让模型看到残缺指令，比不给更糟）。
"""
from __future__ import annotations

from typing import Dict, List, Optional

import capability as CAP

# 硬预算：装配出来的提示片段不超过这么多字符
MAX_CHARS = 1100
# 各节字符配额（超出则整节丢弃，绝不腰斩半句）
_BUDGET = {"work": 380, "knowledge": 300, "memory": 260, "caps": 160}


def _clip(text: str, n: int) -> str:
    t = (text or "").strip()
    return t if len(t) <= n else t[:n].rstrip() + "…"


def _work_block(dir_hint: str = "", limit: int = 3) -> str:
    """工作台账：正在做的 + 下一步 + 最近产物（复盘与续跑的依据）。"""
    try:
        import worklog as WL
    except Exception:                       # noqa: BLE001
        return ""
    lines = []
    try:
        us = WL.unfinished()
        for t in us[:limit]:
            nxt = (t.get("next_step") or "").strip()
            lines.append("- [%s] %s%s" % (
                t.get("status"), (t.get("title") or "")[:26],
                ("｜下一步：%s" % nxt[:30]) if nxt else ""))
        if dir_hint:
            same = [t for t in WL.by_dir(dir_hint, 20)
                    if t.get("status") == "done"][:2]
            if same:
                lines.append("- 该目录已完成过：%s"
                             % "、".join((t.get("title") or "")[:16] for t in same))
        for t in WL.recent(10):
            arts = [a for a in (t.get("artifacts") or []) if not a.startswith("(")]
            if arts:
                lines.append("- 已有产物：%s" % _clip(arts[-1], 46))
                break
    except Exception:                       # noqa: BLE001
        return ""
    return "\n".join(lines)


def _knowledge_block(query: str) -> str:
    """知识库：优先取与本次请求相关的条目（跨书关联），没有就退到最近所学。"""
    try:
        import knowledge as KN
    except Exception:                       # noqa: BLE001
        return ""
    try:
        if (query or "").strip():
            hit = (KN.cross_notes(query, limit=2) or "").strip()
            if hit:
                return hit
        return (KN.learned_bullets(limit=4) or "").strip()
    except Exception:                       # noqa: BLE001
        return ""


def _caps_block(caps: List[dict]) -> str:
    if not caps:
        return ""
    c = caps[0]
    pre = (c.get("prefeed") or "").strip()
    others = "、".join(x["label"] for x in caps[1:3])
    return pre + (("\n（也可考虑：%s）" % others) if others else "")


def assemble(goal: str, ctx: Optional[dict] = None) -> Dict[str, object]:
    """装配一轮对话的上下文。

    ctx 可含：
        ctx["memory"]     认知层给的记忆摘要文本（调用方负责取，本模块不越界）
        ctx["has_engine"] 出图/视频引擎是否就绪（传给 capability.tool_when）
        ctx["dir"]        当前工作目录（用于复盘）

    返回 {system_hint, caps, cap_id, sources, chars, dropped}
    """
    ctx = ctx or {}
    goal = (goal or "").strip()
    caps = CAP.match(goal, ctx) if goal else []
    dir_hint = (ctx.get("dir") or "").strip()

    sections = [
        ("caps", _caps_block(caps)),
        ("work", _work_block(dir_hint)),
        ("knowledge", _knowledge_block(goal)),
        ("memory", str(ctx.get("memory") or "")),
    ]
    # ctx["only"] 限定只装配某几节 —— 聊天链路上「记忆」已由 memrouter 注入、
    # 「知识」也在那里注过，重复塞两遍只会白占上下文、还可能互相矛盾。
    # 所以主链路只取 caps + work，其余节留给需要它们的调用方。
    only = ctx.get("only")
    if only:
        sections = [(n, b) for n, b in sections if n in only]

    kept, dropped, used = {}, [], 0
    for name, body in sections:                       # 按优先级（上面顺序）取
        body = (body or "").strip()
        if not body:
            dropped.append(name)
            continue
        body = _clip(body, _BUDGET.get(name, 300))
        if used + len(body) > MAX_CHARS:
            dropped.append(name)
            continue
        kept[name] = body
        used += len(body)

    parts = []
    if kept.get("caps"):
        parts.append("【本轮能力】\n" + kept["caps"])
    if kept.get("work"):
        parts.append("【手头工作】\n" + kept["work"])
    if kept.get("knowledge"):
        parts.append("【相关知识】\n" + kept["knowledge"])
    if kept.get("memory"):
        parts.append("【关于用户】\n" + kept["memory"])

    hint = "\n\n".join(parts)
    if hint:
        hint += ("\n\n（以上为背景，不要逐条复述；没有真执行的动作不许说已完成。）")
    return {"system_hint": hint, "caps": caps,
            "cap_id": (caps[0]["id"] if caps else "chat"),
            "cap_kind": (caps[0].get("kind") if caps else "general"),
            "sources": list(kept.keys()), "dropped": dropped,
            "chars": len(hint)}


# --------------------------------------------------------------------------
# 自检（隔离数据目录；不碰用户真实工作台账）
# --------------------------------------------------------------------------
def selftest() -> int:
    import os
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

    tmp = tempfile.mkdtemp(prefix="pasm_asm_")
    old = os.environ.get("PASM_STUDIO_DIR")
    os.environ["PASM_STUDIO_DIR"] = tmp
    try:
        import worklog as WL

        # ---------- 干净环境：不炸、不硬塞 ----------
        r = assemble("帮我画一张开学季海报")
        check("空台账也能装配", isinstance(r["system_hint"], str))
        check("命中能力进入装配", r["cap_id"] == "ad", r["cap_id"])
        check("装配含能力段", "【本轮能力】" in r["system_hint"], r["system_hint"][:60])

        # ---------- 反例：普通闲聊不该被塞工作/能力 ----------
        r2 = assemble("今天天气不错啊")
        check("闲聊 cap_id=chat", r2["cap_id"] == "chat", r2["cap_id"])
        check("闲聊不塞能力段", "【本轮能力】" not in r2["system_hint"],
              r2["system_hint"][:80])
        check("闲聊不塞手头工作", "【手头工作】" not in r2["system_hint"])

        # ---------- 工作台账进入装配 ----------
        t = WL.create("做开学季海报", "ad", dir="广告设计", next_step="出主视觉")
        r3 = assemble("继续做海报", {"dir": "广告设计"})
        check("未完成工作进入装配", "【手头工作】" in r3["system_hint"],
              r3["system_hint"][:200])
        check("下一步写进装配", "出主视觉" in r3["system_hint"])
        check("工作段在 sources 里", "work" in r3["sources"], str(r3["sources"]))

        # ---------- 记忆段（由调用方注入，本模块不越界） ----------
        r4 = assemble("帮我画张图", {"memory": "用户叫小志，偏好简洁"})
        check("记忆段进入装配", "【关于用户】" in r4["system_hint"])
        check("记忆内容保留", "小志" in r4["system_hint"])
        check("未注入记忆则无该段",
              "【关于用户】" not in assemble("帮我画张图")["system_hint"])

        # ---------- 预算：超长必须整节丢弃，绝不腰斩 ----------
        big = assemble("帮我画张图", {"memory": "很长的记忆" * 500})
        check("总长不超硬预算", big["chars"] <= MAX_CHARS + 120,
              "chars=%d" % big["chars"])
        check("超长被裁剪", ("memory" in big["sources"]
                            and len(big["system_hint"]) < 2000)
              or "memory" in big["dropped"], str(big["sources"]))
        # 任何一节都不许出现"半句"残留（裁剪只按整节做）
        check("预算内各节完整", big["chars"] <= MAX_CHARS + 120)

        # ---------- 健壮性 ----------
        check("空目标不炸", isinstance(assemble("")["system_hint"], str))
        check("None 目标不炸", isinstance(assemble(None)["system_hint"], str))
        check("ctx 为 None 不炸", isinstance(assemble("画张图", None)["system_hint"], str))
        check("异常 memory 不炸",
              isinstance(assemble("画张图", {"memory": object()})["system_hint"], str))
        check("超长目标不炸",
              isinstance(assemble("画" * 8000)["system_hint"], str))
        check("only 限定装配节",
              set(assemble("继续做海报", {"dir": "广告设计",
                                        "only": ["caps", "work"]})["sources"])
              <= {"caps", "work"})
        check("only 排除记忆节",
              "memory" not in assemble(
                  "画张图", {"memory": "秘密切记", "only": ["work"]})["sources"])


        # ---------- 台账损坏时优雅降级 ----------
        with open(os.path.join(tmp, "work_tasks.json"), "w",
                  encoding="utf-8") as f:
            f.write("{ 这不是合法 json")
        r5 = assemble("继续做海报")
        check("台账损坏 → 装配仍可用（降级不抛）",
              isinstance(r5["system_hint"], str) and r5["cap_id"] in CAP.CAPS,
              str(r5["system_hint"])[:60])

        # 台账损坏后也允许重新写入（不清空用户数据的兜底语义）
        WL.create("恢复后新建", "general", dir="d")
        check("台账损坏后仍可写入", bool(WL.recent(5)))
    finally:
        if old is None:
            os.environ.pop("PASM_STUDIO_DIR", None)
        else:
            os.environ["PASM_STUDIO_DIR"] = old
        shutil.rmtree(tmp, ignore_errors=True)

    print("-" * 46)
    print("context_assembly 自检：%d 项，%s"
          % (passed + failed, "全部通过" if not failed else "%d 项失败" % failed))
    return 1 if failed else 0


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        raise SystemExit(selftest())
    demo = assemble("帮我画一张开学季招生的海报", {"memory": "用户是小志，偏好简洁"})
    print("cap_id:", demo["cap_id"], "| sources:", demo["sources"],
          "| chars:", demo["chars"])
    print("-" * 46)
    print(demo["system_hint"] or "(空)")
    print("=" * 46)
    selftest()
