"""prompts —— PASM 结构化提示词模板（v0.22.0）
================================================================
目标：让不同底层 LLM（云端 DeepSeek/GPT、本地 Ollama qwen/gemma…）
在 PASM 框架下表现趋于一致——统一按「任务类型 → 目标 / 步骤 / 输出格式」
的结构化模板下发指令，减少模型个体差异带来的质量波动。

三类任务模板（可组合）：
  · summarize  总结/提炼：给定材料 → 结构化摘要
  · reasoning  推理/分析：问题 → 先列依据再给结论
  · generation 生成/创作：需求 → 直接给完整成品
  · code       写代码：需求 → 可运行代码
  · chat       闲聊：不走模板（保持拟人，绝不套壳）

用法（桌面同目录模块，无 UI 依赖，可独立单测）：
    from prompts import classify_task, build_prompt
    task = classify_task(user_text)
    user, system_extra = build_prompt(task, user_text)
"""
from __future__ import annotations

import re
from typing import Tuple

# ------------------------------------------------------------------
# 任务类型判定（规则优先，零成本）
# ------------------------------------------------------------------
_RE_CODE = re.compile(
    r"(写|做个?|开发|实现|修复|调试|优化|运行)[^。；\n]{0,24}"
    r"(脚本|代码|程序|函数|网页|接口|爬虫|工具|类|页面|html|js|python|java|go|sql|css)"
    r"|(python|javascript|java|go|sql|html|css|代码)", re.I)
_RE_SUMMARY = re.compile(
    r"(总结|概括|归纳|提炼|摘要|要点|划重点|读书笔记|缩写)"
    r"|(总结一下|概括一下|帮我提炼)")
_RE_REASON = re.compile(
    r"(为什么|怎么回事|如何?分析|利弊|优劣|比较|评估|判断|推理|原因|建议|怎么选|哪个好|风险)")
_RE_EXTRACT = re.compile(r"(提取|抽取|整理成|列成|转成)(表格|清单|列表)")

_TEMPLATES = {
    "code": {
        "goal": "产出一段可直接运行、语法正确、无占位符的完整代码",
        "steps": ("1) 先在心里确认语言、输入输出与边界情况；"
                  "2) 写出完整代码（含必要注释与错误处理）；3) 自查语法与变量名后再输出。"),
        "format": "只输出一个代码块（```语言 开头），代码前最多一句话说明，代码后不写空话。",
    },
    "summarize": {
        "goal": "把给定材料提炼成忠实、有信息量的结构化摘要",
        "steps": ("1) 通读材料，找出核心观点与关键事实；2) 过滤套话、广告、重复内容；"
                  "3) 按重要性排序输出。"),
        "format": "先一行总体结论，再分条列点（每条一个信息点，不写'总之'式空话）。",
    },
    "reasoning": {
        "goal": "给出有依据、可检验的分析结论或建议",
        "steps": ("1) 先明确问题与评价标准；2) 列出关键依据/事实（可分正反两面）；"
                  "3) 由依据推出结论；4) 给出可执行的建议。"),
        "format": "结论先行一句话；然后【依据】分条；最后【建议】分条。不编造事实，不确定要说明。",
    },
    "generation": {
        "goal": "直接产出完整、可直接使用的成品（文案/文档/方案/创意…）",
        "steps": ("1) 明确受众、场景与风格；2) 先定结构再填充内容；"
                  "3) 成稿自查一遍：完整、无占位、无'以下是草稿'式说明。"),
        "format": "直接输出成品本身，第一句就是内容；不要解释过程、不要反问、不要能力清单。",
    },
    "extract": {
        "goal": "从给定内容中按要求抽取并整理信息",
        "steps": "1) 逐段扫描定位目标信息；2) 去重归并；3) 按要求的形态整理。",
        "format": "严格按要求的形态输出（表格/清单），不要附加评论。",
    },
}


def classify_task(text: str) -> str:
    """把用户消息判成 summarize / reasoning / generation / code / extract / chat。"""
    t = (text or "").strip()
    if not t:
        return "chat"
    if _RE_CODE.search(t):
        return "code"
    if _RE_EXTRACT.search(t):
        return "extract"
    if _RE_SUMMARY.search(t):
        return "summarize"
    if _RE_REASON.search(t):
        return "reasoning"
    if len(t) >= 60 and re.search(r"(写|做|生成|创作|拟|起草|策划)", t[:24]):
        return "generation"
    return "chat"


def build_prompt(task: str, user_text: str) -> Tuple[str, str]:
    """返回 (包装后的 user 消息, 追加到 system 的结构化要求)。

    chat 任务原样返回（不套模板——闲聊套壳会立刻假）。
    """
    tpl = _TEMPLATES.get(task)
    if not tpl or not (user_text or "").strip():
        return user_text or "", ""
    sys_extra = (f"\n【本次任务规格】类型：{task}\n"
                 f"目标：{tpl['goal']}\n步骤：{tpl['steps']}\n"
                 f"输出格式：{tpl['format']}\n"
                 f"（按规格执行，不要把本段复述给用户）")
    return user_text, sys_extra


def build_step_prompt(step_title: str, step_detail: str, overall: str) -> Tuple[str, str]:
    """规划器的单步执行提示：每一步都按"生成"规格走，聚焦本步、不越界。"""
    user = (f"【总任务】{overall}\n"
            f"【本步要做】{step_title}"
            + (f"——{step_detail}" if step_detail else "")
            + "\n只完成本步的内容，不要替后面的步骤收尾。")
    sys_extra = ("\n【本次任务规格】类型：规划内单步生成\n"
                 "目标：把这一步做成完整可用的内容（不是提纲、不是思路）\n"
                 "步骤：聚焦本步目标；信息足够就直接产出成品\n"
                 "输出格式：直接输出本步成品；开头不要重复步骤名，结尾不要总结下一步。")
    return user, sys_extra
