"""self_evolve —— 自进化：把干过的活沉淀成技能（v0.28.x）
==========================================================

对齐 Hermes Agent 的「自进化 skill」能力 —— 也是 PASM 一直缺的那一环：
**干完一次，下次不用从零教。**

做法分三步：
1. **复盘**（`from_task`）：从工作台账里捞出一条已完成的任务，连同它的产出、
   附件、步骤记录。
2. **提炼**（`craft_body`）：套用标准 SKILL.md 结构写成一份技能正文
   （目标 / 何时使用 / 执行步骤 / 输出结构 / 质量红线）。
   有模型可用时让它润色一版（`brain=` 传进来即可）；没有模型时用结构化模板，
   **绝不编造用户没做过的步骤**。
3. **入库**（`skillstore.add_skill`）：写进用户技能库，立刻可被检索与复用
   —— 从此 PASM 的"肌肉记忆"多了一条。

还提供 `candidates()`：扫台账找出**重复出现过 ≥2 次**的工作类型，
主动提示"这类活你干了好几次了，要不要我存成技能？"——这才是自进化的入口。
"""
from __future__ import annotations

from typing import Callable, List, Optional, Tuple

try:
    import skillstore as SS
except Exception:                                # 独立导入兜底
    SS = None

# 工作类型 → 人话（用于技能命名与描述）
KIND_CN = {
    "genppt": "做 PPT", "gendoc": "写 Word 文档", "genxls": "做 Excel 表格",
    "script": "写代码", "project": "做项目", "copy": "写文案",
    "skill": "跑技能", "image": "生成图片", "video": "生成视频",
    "manga": "做漫剧", "web": "上网查资料", "weblearn": "上网自学",
    "tableana": "分析表格", "readfile": "读文件", "dirscan": "目录体检",
    "sysops_clean": "清理系统", "general": "通用任务",
}

_OK_WORDS = ("done", "ok", "success", "succeeded", "finished", "completed")


def _is_ok(task: dict) -> bool:
    st = str(task.get("status") or "").lower()
    if st in _OK_WORDS:
        return True
    if "fail" in st or "error" in st or "abort" in st:
        return False
    return bool(task.get("ok"))


def _kind_cn(kind: str) -> str:
    return KIND_CN.get(str(kind or "").strip(), str(kind or "任务"))


# ---------------- 正文提炼 ----------------
def craft_body(title: str, task: str, steps: Optional[List[str]] = None,
               result: str = "", artifacts: Optional[List[str]] = None,
               brain: Optional[Callable[..., str]] = None) -> str:
    """生成技能正文（SKILL.md 的正文部分，不含 frontmatter）。

    brain 给了就用模型润色一版；没给就用结构化模板。
    **两种路径都不允许编造用户没提供的步骤** —— 宁可步骤少而真。
    """
    steps = [str(s).strip() for s in (steps or []) if str(s).strip()]
    artifacts = [str(a).strip() for a in (artifacts or []) if str(a).strip()]
    result = (result or "").strip()

    if brain is not None:
        try:
            sys_p = ("你是技能文档工程师。把用户这次**真实做过**的一件事，"
                     "写成一份可复用的工作规程（给 AI 看的技能卡）。\n"
                     "硬要求：① 只用用户给的事实，**绝不编造他没做过的步骤**；"
                     "② 用简体中文；③ 严格按下面四节输出，不要多余寒暄：\n"
                     "## 目标\n## 何时使用\n## 执行步骤\n## 输出结构\n## 质量红线\n"
                     "④ 「何时使用」里要写清什么情况**不该**用；"
                     "⑤ 「质量红线」列 2-4 条不许做的事。")
            user = ("【这件事】%s\n\n【用户原话/需求】%s\n\n【实际执行步骤】\n%s\n\n"
                    "【最终产出】\n%s"
                    % (title, task or "（未记录）",
                       "\n".join("- " + s for s in steps) or "（未记录）",
                       result[:1200] or "（未记录）"))
            txt = str(brain(sys_p, user, task="skill") or "").strip()
            if len(txt) > 80:
                head = "# %s\n\n" % title
                tail = ""
                if artifacts:
                    tail = ("\n\n## 参考产出\n"
                            + "\n".join("- `%s`" % a for a in artifacts[:5]))
                return head + txt + tail
        except Exception:
            pass          # 模型不可用 → 退回模板，不影响沉淀

    lines = ["# %s" % title, "", "## 目标", (task or "把这件事按同样的标准再做一遍。"), "",
             "## 何时使用"]
    lines.append("· 需要再做一次「%s」这类活的时候。" % title)
    lines.append("· 不该用时：需求与上面明显不同（换主题/换受众/换格式）时，"
                 "按新需求重新做，别硬套这份规程。")
    lines.append("")
    lines.append("## 执行步骤")
    if steps:
        for i, s in enumerate(steps, 1):
            lines.append("%d. %s" % (i, s))
    else:
        lines.append("1. 先跟用户确认交付物形态（文件类型 / 篇幅 / 受众）。")
        lines.append("2. 按确认的形态收集素材，缺什么先问，不要凭空编。")
        lines.append("3. 产出完整成品文件，不要只给大纲或思路。")
        lines.append("4. 交付时说明文件放在哪，并给出一句话摘要。")
    lines.append("")
    lines.append("## 输出结构")
    if result:
        lines.append("参照上次的产出（要点摘录）：")
        for ln in result.splitlines()[:8]:
            if ln.strip():
                lines.append("· " + ln.strip()[:120])
    else:
        lines.append("· 一份可直接使用的完整成品（文件形式），而非聊天里的建议。")
    if artifacts:
        lines.append("")
        lines.append("历史产物参考：")
        for a in artifacts[:5]:
            lines.append("· `%s`" % a)
    lines.append("")
    lines.append("## 质量红线")
    lines.append("· 不编造事实、数据、引用与链接。")
    lines.append("· 不假装已生成文件；没生成就说没生成。")
    lines.append("· 不省略关键内容（禁止「此处省略」「同理可得」）。")
    return "\n".join(lines)


# ---------------- 沉淀 ----------------
def distill(title: str, task: str = "", steps: Optional[List[str]] = None,
            result: str = "", artifacts: Optional[List[str]] = None,
            keywords: str = "", category: str = "自进化",
            brain: Optional[Callable[..., str]] = None) -> Tuple[bool, str]:
    """把一次经验沉淀成技能。返回 (成功?, 给用户看的回复)。"""
    if SS is None:
        return False, "技能库不可用（skillstore 没加载上），这次先存不了。"
    title = (title or "").strip()[:40]
    if not title:
        return False, "要沉淀的话，先给它起个名字吧（比如「周报生成」）。"
    name = "自进化·" + title
    body = craft_body(title, task, steps, result, artifacts, brain=brain)
    kws = (keywords or "").strip()
    if not kws:
        guess = [w for w in _split_words(title + " " + (task or ""))][:8]
        kws = ",".join(guess) if guess else title
    try:
        path = SS.add_skill(name=name, description="（自进化沉淀）%s" % (task or title)[:80],
                            keywords=kws, body=body, category=category,
                            skill_type="skill")
    except Exception as ex:
        return False, "写入技能库失败：%s" % ex
    return True, ("✅ 已沉淀成技能《%s》。下次你说到相关的活，我会照这份规程来做。\n"
                  "· 触发词：%s\n· 文件：`%s`\n"
                  "· 想改随时说「改一下技能 %s」。" % (name, kws, path, name))


def _split_words(text: str) -> List[str]:
    import re
    return [w for w in re.findall(r"[\u4e00-\u9fa5]{2,4}|[A-Za-z]{3,}", text or "")
            if len(w) >= 2]


def from_task(tid: str = "", brain: Optional[Callable[..., str]] = None) -> Tuple[bool, str]:
    """从工作台账里挑一条已完成的任务来沉淀（不传 tid 就取最近一条完成的）。"""
    try:
        import worklog as WL
    except Exception:
        return False, "读不到工作台账（worklog 没加载上）。"
    task = None
    if tid:
        task = WL.get(tid)
    else:
        for t in reversed(WL.recent(40)):
            if _is_ok(t):
                task = t
                break
    if not task:
        return False, ("台账里还没找到已完成的工作。等我帮你干完一件正事，"
                       "再说「把刚才这个存成技能」就行。")
    steps = []
    try:
        for msg in (WL.get_chat(task["id"]) or []):
            if str(msg.get("role")) == "assistant":
                txt = str(msg.get("content") or "")
                for ln in txt.splitlines():
                    ln = ln.strip()
                    if ln.startswith(("1.", "2.", "3.", "4.", "5.", "·", "-")) and len(ln) > 6:
                        steps.append(ln.lstrip("·- ").strip())
        steps = steps[:8]
    except Exception:
        pass
    return distill(title=task.get("title") or _kind_cn(task.get("kind", "")),
                   task=task.get("progress") or task.get("title") or "",
                   steps=steps,
                   artifacts=task.get("artifacts") or [],
                   keywords=_kind_cn(task.get("kind", "")),
                   brain=brain)


# ---------------- 主动建议 ----------------
def candidates(min_times: int = 2, scan: int = 60) -> List[dict]:
    """扫台账，找出「重复干过 ≥min_times 次」的工作类型 → 值得沉淀的候选。"""
    try:
        import worklog as WL
    except Exception:
        return []
    bucket: dict = {}
    for t in WL.recent(scan):
        if not _is_ok(t):
            continue
        k = str(t.get("kind") or "general")
        b = bucket.setdefault(k, {"kind": k, "count": 0, "titles": []})
        b["count"] += 1
        title = str(t.get("title") or "")
        if title and title not in b["titles"]:
            b["titles"].append(title)
    out = [b for b in bucket.values() if b["count"] >= max(2, int(min_times))]
    out.sort(key=lambda x: -x["count"])
    return out


def suggest_text(min_times: int = 2) -> str:
    """把"要不要沉淀成技能"这个建议说成人话。"""
    cands = candidates(min_times)
    if not cands:
        return ("📈 目前还没有明显重复的工作类型。等我帮你多干几件同类的事，"
                "我就能把它们沉淀成技能了。")
    lines = ["📈 我发现这几类活你干过好几次了，要不要我沉淀成技能？\n"
             "（沉淀完下次直接说一句就能按同样标准做）\n"]
    for i, c in enumerate(cands, 1):
        lines.append("%d. **%s** —— 成功 %d 次\n   涉及：%s"
                     % (i, _kind_cn(c["kind"]), c["count"],
                        "、".join(c["titles"][:3]) or "—"))
    lines.append("\n想沉淀哪条就说「把%d沉淀成技能」。" % 1)
    return "\n".join(lines)
