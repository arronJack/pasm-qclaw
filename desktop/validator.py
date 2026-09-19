"""validator —— PASM 输出验证器（v0.22.0）
================================================================
对 LLM 产出做零成本规则校验：不通过 → 带具体问题重新生成一次，
仍不通过 → 如实降级输出（绝不静默假装成功）。

检查维度：
  · 代码：```代码块提取 → Python 用 ast 语法树真编译检查；
    JS/Java/Go/C 做括号/引号配平 + 常见截断特征；空代码/占位符判失败。
  · JSON：```json 块或全文 → json.loads。
  · 通用：非空；生成类长度下限；拒绝式/占位式话术（"无法""作为AI""略"）判失败；
    Markdown 代码块未闭合判截断。

用法（无 UI 依赖，可独立单测）：
    from validator import check
    ok, problems = check("code", llm_output, lang="python")
"""
from __future__ import annotations

import ast
import json
import re
from typing import List, Tuple

_CODE_FENCE = re.compile(r"```[ \t]*(\w+)?[ \t]*\n(.*?)```", re.S)
_JSON_FENCE = re.compile(r"```[ \t]*json[ \t]*\n(.*?)```", re.S | re.I)

_REFUSAL_RE = re.compile(
    r"(无法完成|不能协助|我无法|作为(一?个)?AI|抱歉.{0,6}无法|没有能力|做不到)")
# 「占位符 / 省略标记」——**必须独立成词**，不能把词里的「略」也算上。
# v0.30.11 修（真实事故）：旧式 `略。?` 是个**裸字符分支**，任何含「略」的词都会被判成占位符。
#   广告模板自带的「（策略层）」「策略来源」直接命中 → **每一份广告方案都过不了质检**，
#   工作流步骤永远回「广告方案未过质检（未标完成）」（小志：工作做得不对）。
#   影响面远超广告：check_general 对 generation/summarize/reasoning/extract 全跑这条，
#   凡产出里出现 策略 / 战略 / 简略 / 忽略 / 大略 的，一律误杀。
#   判据必须**能证伪**：真占位符抓得住（selftest 正例），普通词放得过（反例）。
_OMIT_L = r"[\s　（(\[【、，。；：:>#*\-—…]"      # 「略」左边允许的分隔符
_OMIT_R = r"[\s　）)\]】、，。；：:<>…]"           # 「略」右边允许的分隔符
_PLACEHOLDER_RE = re.compile(
    r"（此处[填放插入].{0,10}）"                          # （此处填写/放入…）
    r"|\(此处.{0,10}\)"                                # (此处…)
    r"|【待补充】|【待补全】|【略】|【省略】"              # 方括号标记
    r"|待补全"
    r"|(以下|此处|其余|余下|全文|正文|内容|后面|中间)"
    r"(内容|部分|正文|文字|段落)?\s*(省略|从略)"     # 「以下省略」「正文从略」
    r"|(?:^|(?<=" + _OMIT_L + r"))略。?(?=$|" + _OMIT_R + r")"      # 独立成词的「略」/「略。」
    r"|（略）|\(略\)",
    re.M,
)


# ---------------- 代码块提取 ----------------
def extract_code(text: str) -> Tuple[str, str]:
    """返回 (代码, 语言)。优先第一个围栏代码块；没有围栏就把全文当代码。"""
    m = _CODE_FENCE.search(text or "")
    if m:
        return (m.group(2) or "").strip(), (m.group(1) or "").lower()
    return (text or "").strip(), ""


def _balanced_pairs(code: str, pairs=((("{", "}"), 1), (("[", "]"), 1), (("(", ")"), 1))) -> List[str]:
    """括号配平检查（忽略字符串/注释里的干扰，够用的工程近似）。"""
    probs: List[str] = []
    # 去掉字符串字面量与注释，避免内容里的括号误报
    cleaned = re.sub(r"'''[\s\S]*?'''|\"\"\"[\s\S]*?\"\"\"", '""', code)
    cleaned = re.sub(r"'[^'\n]*'", "''", cleaned)
    cleaned = re.sub(r'"[^"\n]*"', '""', cleaned)
    cleaned = re.sub(r"#[^\n]*", "", cleaned)
    cleaned = re.sub(r"//[^\n]*", "", cleaned)
    for (o, c), _w in pairs:
        if cleaned.count(o) != cleaned.count(c):
            probs.append(f"括号不配平：{o} {cleaned.count(o)} 个 / {c} {cleaned.count(c)} 个")
    return probs


def check_code(text: str, lang: str = "") -> Tuple[bool, List[str]]:
    code, fence_lang = extract_code(text)
    lang = (lang or fence_lang or "").lower()
    if not code:
        return False, ["没有产出任何代码"]
    probs: List[str] = []
    if _REFUSAL_RE.search(code):
        probs.append("代码里混入了拒绝式话术（疑似没写完就解释）")
    if len(code) < 15:
        probs.append("代码过短（不足 15 字符），疑似截断或敷衍")
    if lang in ("python", "py", ""):
        try:
            ast.parse(code)
        except SyntaxError as ex:
            probs.append(f"Python 语法错误：第 {ex.lineno} 行 {ex.msg}")
    elif lang in ("json",):
        ok2, p2 = check_json(text)
        probs.extend(p2)
    else:
        probs.extend(_balanced_pairs(code))
        if re.search(r"```\s*$", text or "") and not _CODE_FENCE.search(text or ""):
            probs.append("代码块没有闭合（疑似被截断）")
    return (not probs), probs


def check_json(text: str) -> Tuple[bool, List[str]]:
    m = _JSON_FENCE.search(text or "")
    raw = (m.group(1) if m else (text or "")).strip()
    if not raw:
        return False, ["没有产出任何 JSON 内容"]
    try:
        json.loads(raw)
        return True, []
    except Exception as ex:
        return False, [f"JSON 解析失败：{ex}"]


def check_general(text: str, task: str = "generation",
                  min_len: int = 30) -> Tuple[bool, List[str]]:
    """通用产出校验：空 / 拒绝话术 / 占位符 / 过短 / 代码块未闭合。"""
    t = (text or "").strip()
    if not t:
        return False, ["回复为空"]
    probs: List[str] = []
    if task in ("generation", "summarize", "reasoning", "extract"):
        if _REFUSAL_RE.search(t[:120]):
            probs.append("开头就是拒绝式话术，没有真正回答")
        if _PLACEHOLDER_RE.search(t):
            probs.append("内容里有占位符（此处省略/待补充）")
        if len(t) < min_len:
            probs.append(f"内容太短（{len(t)} 字，要求 ≥{min_len} 字），疑似敷衍")
        fences_open = len(re.findall(r"```", t))
        if fences_open % 2 == 1:
            probs.append("代码块未闭合（回复疑似被截断）")
    return (not probs), probs


def check(task: str, text: str, lang: str = "", min_len: int = 30) -> Tuple[bool, List[str]]:
    """统一入口：按任务类型路由到对应校验。"""
    if task in ("code", "script"):
        return check_code(text, lang)
    if task == "json":
        return check_json(text)
    return check_general(text, task=task, min_len=min_len)


def selftest():
    """占位符判据的**可证伪**自检（v0.30.11）。

    正例：真占位符必须抓到；反例：含「略」的普通词**不许**误判
    （历史事故：裸 `略。?` 分支把「策略」判成占位符 → 每份广告方案都过不了质检）。
    """
    pos = [
        "（此处填写品牌名）",
        "解释：(此处略)",
        "- 结论：【待补充】",
        "这部分待补全",
        "以下省略若干段落",
        "正文从略",
        "（略）",
        "略。",
    ]
    neg = [
        "## 创意说明（策略层）",
        "- 策略来源：离线模板",
        "投放策略：社交媒体 / 信息流 / 官网首屏",
        "战略上聚焦，战术上灵活",
        "简略说明：主体突出、留白克制",
        "忽略该字段即可",
        "大略估算约三成",
    ]
    bad = []
    for s in pos:
        if not _PLACEHOLDER_RE.search(s):
            bad.append("漏抓占位符：%r" % s)
    for s in neg:
        if _PLACEHOLDER_RE.search(s):
            bad.append("误判普通词：%r" % s)
    for b in bad:
        print("  [FAIL] " + b)
    print("validator 占位符判据：正 %d / 反 %d · %s"
          % (len(pos), len(neg), "全部通过" if not bad else "失败 %d 项" % len(bad)))
    return not bad


if __name__ == "__main__":
    import sys as _sys
    _sys.exit(0 if selftest() else 1)
