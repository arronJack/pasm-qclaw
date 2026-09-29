# -*- coding: utf-8 -*-
"""domain_advisor_bridge —— v0.31.11 领域顾问桥。

把 ``pasm-domain-advisor``（PyPI 包 ``pasm-domain-advisor``，引擎
``pasm_da``）的「前提抽取 + 已核验规则 + 自检闸门」接进小U聊天管线：

1. ``digest(user_text)``：用户消息命中某领域 profile 时，产出一段系统提示
   注入块（已核验规则 + 前提约束 + 剖析框架），由 ``_build_system`` 拼进
   上下文 —— 让小U 先吃到"现行规则"，再开口。
2. ``gate_reply(user_text, reply)``：回复生成后按领域前提跑自检闸门，
   命中"自相矛盾"（如：用户已有执照却建议先开个人店）时在回复尾部追加
   **纠偏段** —— 自检结果对用户可见（诚实守则），不是静默改写。

设计约束（都踩过坑）：
- ``pasm_da`` 是**可选依赖**：未安装 / 导入失败一律静默降级，小U 行为
  与 0.31.10 完全一致 —— 桥永远不能把聊天搞挂。
- 纯 stdlib、零 Qt：可以脱离桌面端单独冒烟（tools/verify_v03111.py）。
- 不碰 pasm/cognitive（fork 铁律）：本模块只是应用层装配，领域知识全部
  声明在 pasm-domain-advisor 的 profile 里，宿主一行业务词都没有。
"""
from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# 注入块大小预算：聊天上下文贵（慢机预算比例见 verify_v307），宁精勿多。
_MAX_KNOWLEDGE = 4          # 最多带 4 条已核验规则
_MAX_COMPLIANCE = 6         # 合规矩阵最多 6 个要点
_MAX_GATE_LINES = 40        # 闸门最多审 40 行，防超长回复拖慢

_DA = None                  # 缓存：None=未探测 / False=不可用 / module


def _da():
    """懒加载 pasm_da；任何失败都归一为 False（降级开关）。"""
    global _DA
    if _DA is None:
        try:
            import pasm_da as m          # noqa: import-outside-toplevel
            _DA = m
        except Exception as _ex:         # 未安装 / 依赖缺失 / 损坏
            logger.info("domain_advisor_bridge: pasm_da 不可用，降级 (%s)", _ex)
            _DA = False
    return _DA or None


def available() -> bool:
    """pasm_da 是否可用（设置页可据此显示"领域顾问：已启用/未安装"）。"""
    return _da() is not None


def match_profile(text: str):
    """领域路由：返回命中的 DomainProfile 或 None（pasm_da 不可用也算 None）。"""
    m = _da()
    if not m:
        return None
    try:
        return m.profiles.match(text or "")
    except Exception:
        return None


# --------------------------------------------------------------- 注入（生成前）
def digest(user_text: str) -> str:
    """命中领域 → 产出系统提示注入块；不命中/不可用 → 空串。"""
    m = _da()
    prof = match_profile(user_text)
    if not m or not prof:
        return ""
    try:
        from pasm_da import gate as G    # 引擎内相对稳定，失败则整块放弃
        premises = G.extract_premises(user_text or "", prof)
    except Exception:
        premises = {}

    parts = ["【领域顾问 · %s】用户的问题落在你精通的领域。"
             "以下规则为**已核验的现行政策**，回答必须以此为基准；"
             "与它们冲突的旧信息一律不要采用。" % prof.name_zh]

    if premises:
        kv = "；".join("%s=%s" % (k, "是" if v else "否")
                       for k, v in sorted(premises.items())) or "（未抽取到）"
        parts.append("已从用户话中抽取的前提：%s —— 建议必须与前提一致"
                     "（例如用户已有某资质时，给「直接用它拿满权益」的路径，"
                     "绝不给降级建议）。" % kv)

    ks = [str(k.get("title", "")).strip()
          for k in (prof.knowledge or []) if k.get("title")]
    if ks:
        parts.append("现行规则要点：\n" + "\n".join(
            "- " + s for s in ks[:_MAX_KNOWLEDGE]))

    cm_points = [pt for pts in (prof.compliance_matrix or {}).values()
                 for pt in pts]
    if cm_points:
        parts.append("合规关键点：\n" + "\n".join(
            "- " + pt for pt in cm_points[:_MAX_COMPLIANCE]))

    if prof.premise_constraints:
        forbids = []
        for c in prof.premise_constraints:
            if c.warn_zh:
                forbids.append(c.warn_zh)
        if forbids:
            parts.append("红线（输出前自查，违反=自相矛盾）：\n" +
                         "\n".join("- " + w for w in forbids))

    dims = [d for d in (prof.analysis_dimensions or [])[:4]]
    if dims:
        parts.append("剖析框架（按这些维度组织回答）：" + " / ".join(dims))

    return "\n".join(parts)


# --------------------------------------------------------------- 闸门（生成后）
_LINE_SPLIT = re.compile(r"[\n；;。]+")
_BULLET = re.compile(r"^[\s\-•·*①②③④⑤⑥⑦⑧⑨⑩\d]+[、.．)）]?\s*")


def gate_reply(user_text: str, reply: str) -> str:
    """对已生成的回复跑前提一致性闸门；命中矛盾 → 尾部追加纠偏段。

    只追加、不改写（诚实守则：小U 承认自检发现的问题，比偷偷改话可信）。
    不可用/不命中/无矛盾 → 原样返回。
    """
    m = _da()
    prof = match_profile(user_text)
    if not m or not prof or not reply:
        return reply
    try:
        from pasm_da import gate as G
        premises = G.extract_premises(user_text or "", prof)
        if not any(premises.values()):
            return reply                    # 没有真前提，闸门无从谈起
        lines = []
        for raw in _LINE_SPLIT.split(reply)[:_MAX_GATE_LINES]:
            ln = _BULLET.sub("", raw).strip()
            if len(ln) >= 6:                # 太短的碎片不算"建议"
                lines.append(ln)
        report = G.verify_advice(lines, premises, prof, [])
        warns = [f.message for f in report.flags
                 if f.level == "contradiction" and f.message]
        if not warns:
            return reply
        # 同一约束只提示一次（warn_zh 在多条命中行里会重复出现）
        seen, uniq = set(), []
        for w in warns:
            key = w.split("：", 1)[-1][:24]
            if key not in seen:
                seen.add(key)
                uniq.append(w)
        footer = ("\n\n---\n⚠️ **自检纠偏**：上面有些说法与你的实际情况冲突，"
                  "请以下面为准——\n" + "\n".join("· " + w for w in uniq[:3]))
        return reply + footer
    except Exception as _ex:
        logger.warning("domain_advisor_bridge: 闸门异常，放行原文 (%s)", _ex)
        return reply
