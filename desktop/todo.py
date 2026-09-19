"""todo —— 它的"帮你工作"技能：待办 + 到期自动提醒。

用法（对话里）：
- "帮我记：周三下午3点开周会"  / "提醒我 14:30 喝水"
- "我有什么待办"  / "待办" → 列出未完成
- "完成 去取快递" → 划掉
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import List, Optional

DATA_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"),
                        "PASMStudio") if os.name == "nt" else os.path.expanduser("~/.pasmstudio")
TODO_FILE = os.path.join(DATA_DIR, "todos.json")


def _load() -> List[dict]:
    try:
        if os.path.exists(TODO_FILE):
            return json.load(open(TODO_FILE, "r", encoding="utf-8"))
    except Exception:
        pass
    return []


def _save(items: List[dict]):
    try:
        json.dump(items, open(TODO_FILE, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
    except Exception:
        pass


def add(text: str, due: Optional[str] = None) -> dict:
    items = _load()
    item = {"text": text, "due": due, "done": False,
            "created": time.strftime("%Y-%m-%d %H:%M")}
    items.insert(0, item)
    _save(items)
    return item


def open_items() -> List[dict]:
    return [i for i in _load() if not i.get("done")]


def done(text_like: str) -> bool:
    items = _load()
    hit = None
    for i in items:
        if not i.get("done") and text_like in i.get("text", ""):
            hit = i
            break
    if hit:
        hit["done"] = True
        _save(items)
        return True
    return False


def parse_time(text: str):
    """解析 'HH:MM' / 'X点Y分' / '明天X点' / '中午/下午X点'，返回 'YYYY-MM-DD HH:MM' 或 None。"""
    now = time.localtime()
    txt = text
    offset = 0
    if "后天" in txt:
        offset = 2
    elif "明天" in txt:
        offset = 1
    hm = re.search(r"(\d{1,2})\s*[:：点时]\s*(\d{1,2})?分?", txt)
    if not hm:
        hm = re.search(r"(\d{1,2})\s*点(?:\s*(\d{1,2}))?", txt)
    if not hm:
        return None
    parts = [g for g in hm.groups() if g]
    hh = int(parts[0]) % 24
    mm = int(parts[1]) if len(parts) > 1 else 0
    t = time.mktime((now.tm_year, now.tm_mon, now.tm_mday + offset,
                     hh, mm, 0, 0, 0, -1))
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(t))


def pop_last() -> Optional[dict]:
    """撤销最近一次新增的未完成待办（供"撤销刚才那条"用）。"""
    items = _load()
    for i, it in enumerate(items):
        if not it.get("done"):
            del items[i]
            _save(items)
            return it
    return None


def handle(text: str) -> Optional[str]:
    """对话助手入口：识别待办意图。命中返回回复文本，否则 None。"""
    raw = (text or "").strip()
    if not raw:
        return None

    # —— 0）"谢谢你提醒我下班…"= 道谢引用，不是让我记待办；先识别，别自作多情 ——
    #      （v0.17.4：修复"道谢被当成记待办、内容里'提醒我'被剥掉"的真机问题）
    if re.search(r"(?:谢谢|谢谢您|感谢|多谢|辛苦).{0,6}(?:提醒|叫我|喊我)", raw):
        # 例外：仍含"帮我/请/麻烦"+记/提醒 的强指令（如"谢谢，请提醒我 15:00 喝水"）→ 放行照记
        if not re.search(r"(?:帮我|请|麻烦).{0,6}(?:提醒|记)", raw):
            if re.search(r"(?:不过|但是|只是|还要|还得|再一会|再忙|忙完|忙会|"
                         r"等下|等会|待会|稍后|晚点|晚些|晚一点)", raw):
                return ("不客气～那你先忙，我不打扰。需要到点喊你的话，"
                        "说「提醒我 15:00 喝水」这样我就给你加个闹钟～")
            return "不客气～小意思。以后要记事情或到点提醒，直接说「提醒我 18:00 交周报」就行～"

    # —— 0.5）撤销刚误记的那条（无需命中待办触发词）——
    if re.search(r"(?:撤销刚才|删掉刚才|删了刚才|撤销上一条|撤销刚才那条|删掉刚才那条|"
                 r"刚才是说错|刚才那条删掉|撤掉刚才)", raw):
        it = pop_last()
        return (f"好，已把刚才那条「{it['text']}」撤掉了，这次不记啦。"
                if it else "刚才没有新记的待办，不用撤销～")

    if "有什么待办" in text or "我的待办" in text or raw in ("待办", "待办列表"):
        items = open_items()
        if not items:
            return "现在没有未完成的待办，我帮你记得很干净～"
        rows = []
        for it in items:
            due = f"（{it['due'][5:16]}）" if it.get("due") else ""
            rows.append(f"{it['text']} {due}")
        return "你的待办有：\n- " + "\n- ".join(rows)
    m = re.search(r"完成\s+(.+)", text)
    if m:
        ok = done(m.group(1).strip())
        return ("好，已划掉「" + m.group(1).strip() + "」✓") if ok else \
            "没找到这一条待办，要不看看别的？"
    if not any(w in text for w in ("待办", "提醒我", "帮我记", "记一下")):
        return None
    # 先剥句首会话壳（"谢谢，请提醒我 15:00 喝水" → "请提醒我 15:00 喝水"），
    # 再剥指令外壳，留下真正要记的内容（时间另用原文解析）
    core = re.sub(r"^(?:好的?|好|嗯|行|可以|收到|知道了|了解|谢谢|谢谢您|感谢|多谢|"
                  r"辛苦了?|麻烦|那个|打扰了|不好意思)[，,。!！~～\s]*", "", text)
    body = re.sub(r"(?:请|麻烦|麻烦你|帮我一下|帮忙|帮我|帮我记|提醒我|记一下|"
                  r"备注|待办[:：]?\s*)", "", core).strip(" ，。：:")
    if not body:
        return "想让我记什么呀？比如：帮我记 明天10点 交周报"
    due = parse_time(text)
    if due:
        # 去掉正文里重复的时间片段："14:00 喝水" → "喝水"
        pat = r"(?:今天|明天|后天)?\s*\d{1,2}\s*[:：点时]\s*\d{0,2}分?"
        body2 = re.sub(pat, "", body).strip(" ，。：:")
        if body2:
            body = body2
    add(body, due)
    if due:
        return f"记住啦：「{body}」——我会在 {due[5:16]} 提醒你。"
    return f"记住啦：「{body}」（没带时间的话，你可以说'提醒我 15:00 做这件事'给它加闹钟）"


def due_now() -> List[dict]:
    """整点分钟匹配，供提醒器使用。"""
    cur = time.strftime("%Y-%m-%d %H:%M")
    return [i for i in open_items() if i.get("due") == cur]
