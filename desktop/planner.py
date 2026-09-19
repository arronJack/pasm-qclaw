"""planner —— PASM 任务规划器（v0.22.0）
================================================================
把复杂用户指令分解成可执行步骤，然后逐步执行（逐步调用 LLM 生成每步内容）。
规则优先（零成本零延迟），规则切不开且确属复杂任务时才用一次轻量 LLM 分解。

设计原则：
  · 简单任务绝不规划（直通不折腾，效率优先）；
  · 规则能拆就用规则：然后/再/接着/第一步…/；/多句多动词；
  · LLM 分解要求输出 JSON 步骤表，解析失败自动退回"整体当一步"；
  · 产出统一为 [{title, detail}]，执行方逐步喂给 LLM + 验证器。

用法（无 UI 依赖，可独立单测）：
    from planner import needs_plan, make_plan
    if needs_plan(text):
        steps = make_plan(text, llm_fn=callable_or_None)
"""
from __future__ import annotations

import json
import re
from typing import Callable, List, Optional

# 复杂度信号
_RE_SEQ = re.compile(r"(然后|接着|再帮我|再帮我?|然后再|第一步|第二步|第三步|之后|最后)")
_RE_MULTI_ACTION = re.compile(
    r"(帮我?|做个?|写个?|生成|开发|整理|统计|分析|翻译|总结|做一份|写一份)")
_RE_CONJ = re.compile(r"(；|;|。|，并且|，同时|，还要|，另外|\n)")

# 需要规划的"重活"关键词
_RE_HEAVY = re.compile(
    r"(开发|全栈|网站|系统|管理工具|小程序|项目|游戏)" )
# 创作管线（图/视频/漫剧）有自己的专业流程，规划器不插手
_RE_OWN_PIPELINE = re.compile(r"(画一?[张张数]?|出图|生成图|短片|漫剧|视频|一张图|表情包)")


def needs_plan(text: str) -> bool:
    """是否值得先出计划再动手（复杂任务才算，简单任务直通）。"""
    t = (text or "").strip()
    if len(t) < 14:                       # 太短的事不值得规划
        return False
    if _RE_OWN_PIPELINE.search(t):        # 图/视频/漫剧走自己的专业管线
        return False
    seq = len(_RE_SEQ.findall(t))
    if seq >= 1 and len(_RE_MULTI_ACTION.findall(t)) >= 2:
        return True
    if _RE_HEAVY.search(t) and len(_RE_MULTI_ACTION.findall(t)) >= 2:
        return True
    # 多分句（≥3 段）且带两个以上动作词：大概率是多步任务
    parts = [p for p in re.split(r"[；;\n。]", t) if p.strip()]
    if len(parts) >= 3 and len(_RE_MULTI_ACTION.findall(t)) >= 2:
        return True
    return False


# 规则切分：先按显式顺序词切，再按分号/句号切（保留动作词开头）
_RE_SPLIT_SEQ = re.compile(
    r"(?=(?:然后|接着|之后|最后|第一步|第二步|第三步|第四步|再帮我))"
)
_STOPW = ("帮我", "请", "麻烦", "你", "先", "再", "然后", "接着")


def rule_steps(text: str) -> List[dict]:
    """规则切分：能切出 ≥2 个带动作的子任务才返回，否则空列表。"""
    t = (text or "").strip()
    if not t:
        return []
    segs = [s.strip(" \t，。；;、") for s in _RE_SPLIT_SEQ.split(t) if s.strip(" \t，。；;、")]
    if len(segs) < 2:
        segs = [p.strip(" \t，。；;、") for p in re.split(r"[；;\n]", t) if p.strip()]
    steps = []
    for s in segs:
        if not _RE_MULTI_ACTION.search(s):
            continue
        title = s
        for w in _STOPW:                  # 剥句首客套/连接词，标题更干净
            if title.startswith(w):
                title = title[len(w):]
        title = title.strip("，。； 　")
        if len(title) >= 3:
            steps.append({"title": title[:60], "detail": ""})
    return steps if len(steps) >= 2 else []


_PLAN_SYS = ("你是严谨的任务规划器。把用户指令分解为 2-5 个可执行步骤。"
             "只输出 JSON 数组，不要解释："
             '[{"title":"步骤名(<=20字)","detail":"这一步具体做什么(<=40字)"}]')


def make_plan(text: str, llm_fn: Optional[Callable[[str, str], str]] = None) -> List[dict]:
    """产出步骤表：规则优先；规则切不开且有 LLM 时用一次轻量分解。

    llm_fn(user_prompt, system_prompt) -> str（由调用方绑定到网关）。
    返回至少 1 步（整体当一步），调用方对单步计划按"无需规划"处理。
    """
    steps = rule_steps(text)
    if steps:
        return steps
    if llm_fn is not None:
        try:
            ans = llm_fn(
                "用户指令：" + (text or "")[:600] +
                "\n\n请把它分解为 2-5 个步骤，只输出 JSON 数组。",
                _PLAN_SYS)
            m = re.search(r"\[[\s\S]*\]", ans or "")
            if m:
                arr = json.loads(m.group(0))
                out = []
                for it in arr[:5]:
                    if isinstance(it, dict) and str(it.get("title", "")).strip():
                        out.append({"title": str(it["title"])[:40].strip(),
                                    "detail": str(it.get("detail", ""))[:80].strip()})
                if len(out) >= 2:
                    return out
        except Exception:
            pass
    return [{"title": (text or "")[:60], "detail": ""}]


def format_plan(steps: List[dict]) -> str:
    """把步骤表排成给人看的计划（对话里展示用）。"""
    marks = "①②③④⑤⑥"
    lines = []
    for i, s in enumerate(steps[:6]):
        d = f"——{s['detail']}" if s.get("detail") else ""
        lines.append(f"{marks[i]} {s['title']}{d}")
    return "\n".join(lines)
