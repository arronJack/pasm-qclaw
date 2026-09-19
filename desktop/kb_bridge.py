# -*- coding: utf-8 -*-
"""kb_bridge.py —— 知识库 ↔ 工作流的回流桥（v0.30.5）

对标 BaiLongma 的「知识库预取缓存」：`knowledge` 原本只往聊天里单向注入，
学到的内容**进不了工作流的执行链** —— 自学了一堆营销知识，让它做海报时
一个都用不上。本模块把这条回流打通，并且**可证伪**：
给定一个目标，能明确说出"命中了哪几条知识、各得几分"，而不是含糊地塞一段文本。

## 闭环

    自学/对话 → knowledge.record()           （已有：写入）
                    ↓
    工作流目标 → kb_bridge.hits(goal)        （本模块：带分数检索）
                    ↓
    kb_bridge.feed(prompt, goal)             （把知识拼进执行器提示词）
                    ↓
    产物 .md / 出图提示词                     （用得上就用）

## 三条诚实边界

1. **够分才回**。沿用聊天注入同一门限（`knowledge._KB_MIN_SCORE`）。
   相关性不够时返回空串 —— 宁可什么都不带，也不塞无关内容干扰生成。
2. **不硬凑**。注入文本末尾明确写「用不上就别用」，避免模型为凑知识而跑题。
3. **失败不阻塞**。知识库坏了（文件损坏 / 无权限）就返回空串，绝不让
   工作流因为"取不到知识"而失败。
"""
from __future__ import annotations

import os

#: 相关性下限 —— 与聊天注入同源，避免两处口径漂移
MIN_SCORE = 3
#: 一次回流最多带几条、最多多少字（防挤占上下文）
MAX_HITS = 3
MAX_CHARS = 520

_TAIL = "（以上是你学过的，用得上的地方自然引用；用不上就别硬凑。）"


def _kb():
    """惰性拿 knowledge 模块（它 import 时会建目录，测试里要能替换路径）。"""
    try:
        import knowledge as K
        return K
    except Exception:  # noqa: BLE001
        return None


def hits(goal: str, limit: int = MAX_HITS, min_score: int = None) -> list:
    """按目标检索相关知识，返回 [{title, bullets, text, score}]。

    这是**可证伪**的入口：分数摆在明面上，能直接验证
    「相关的进得来 / 无关的进不来」，而不是只看最终生成效果。
    """
    K = _kb()
    g = (goal or "").strip()
    if not K or not g:
        return []
    thr = MIN_SCORE if min_score is None else int(min_score)
    try:
        know = K._load_know()
    except Exception:  # noqa: BLE001
        return []
    if not know:
        return []
    low = g.lower()
    try:
        grams = K._query_grams(g, 30)
    except Exception:  # noqa: BLE001
        return []
    out = []
    for e in know:
        try:
            s = K._kb_score(e, low, grams)
        except Exception:  # noqa: BLE001
            continue
        if s >= thr:
            out.append({"title": e.get("title", ""),
                        "bullets": list(e.get("bullets", [])[:3]),
                        "text": (e.get("text") or "")[:400],
                        "src": e.get("src", ""),
                        "score": s})
    out.sort(key=lambda x: -x["score"])
    return out[:max(1, int(limit))]


def digest(goal: str, limit: int = MAX_HITS, max_chars: int = MAX_CHARS) -> str:
    """把命中的知识压成一段可注入文本；没有够分的知识就返回空串。"""
    hs = hits(goal, limit=limit)
    if not hs:
        return ""
    lines = ["【你学过、且与当前任务相关的知识】"]
    for h in hs:
        body = "；".join(h["bullets"]) or (h["text"][:80].replace("\n", " ") + "…")
        lines.append("· 《%s》—— %s" % (h["title"], body))
    txt = "\n".join(lines)
    if len(txt) > max_chars:
        # 按整条砍，不腰斩某条要点（半句话比没有更糟）
        keep = [lines[0]]
        for ln in lines[1:]:
            if len("\n".join(keep + [ln])) > max_chars:
                break
            keep.append(ln)
        txt = "\n".join(keep) if len(keep) > 1 else lines[0]
    return txt


def feed(prompt: str, goal: str = "", limit: int = MAX_HITS) -> str:
    """把相关知识拼进提示词（放在最前面，让模型先看到）。命中为空则原样返回。"""
    d = digest(goal or prompt, limit=limit)
    if not d:
        return prompt or ""
    return "%s\n%s\n\n%s" % (d, _TAIL, prompt or "")


def learn(title: str, bullets=None, text: str = "", src: str = "自学") -> int:
    """把一次学习的成果结构化写回知识库（薄封装，便于执行器统一调用）。"""
    K = _kb()
    if not K or not (title or "").strip():
        return -1
    try:
        return K.record((title or "").strip(), list(bullets or []),
                        text=text or "", src=src or "自学")
    except Exception:  # noqa: BLE001
        return -1


def applied_note(goal: str) -> str:
    """一行"这次用到了哪几条知识"，给面板/日志展示（不参与生成）。"""
    hs = hits(goal)
    if not hs:
        return ""
    return "用到知识：" + "、".join("%s(%d分)" % (h["title"], h["score"]) for h in hs)


# --------------------------------------------------------------------------
# 自检
# --------------------------------------------------------------------------
def _selftest() -> int:
    import shutil
    import tempfile
    fails = []
    n = {"v": 0}

    def check(name, cond, extra=""):
        n["v"] += 1
        print("  %s %s%s" % ("[OK]  " if cond else "[FAIL]", name,
                             ("  " + str(extra)) if (extra and not cond) else ""))
        if not cond:
            fails.append(name)

    print("== kb_bridge 自检 ==")
    K = _kb()
    if not K:
        print("  [SKIP] knowledge 模块不可用")
        return 0
    tmp = tempfile.mkdtemp(prefix="kb_")
    saved = (K.KNOW_FILE, K.BOOKS_DIR)
    try:
        # 隔离到临时库 —— 绝不能碰用户真实知识库
        K.KNOW_FILE = os.path.join(tmp, "knowledge.json")
        K.BOOKS_DIR = os.path.join(tmp, "books")
        os.makedirs(K.BOOKS_DIR, exist_ok=True)
        K._KNOW_CACHE["ts"] = 0.0
        K._KNOW_CACHE["data"] = None

        # --- 1. 空库：什么都不回（不是硬凑） ---
        check("空库 → hits 为空", hits("做一份开学季海报") == [])
        check("空库 → digest 为空串", digest("做一份开学季海报") == "")
        check("feed 无知识时原样返回", feed("写个标题", "做海报") == "写个标题")

        # --- 2. 写入知识后，相关目标能命中 ---
        learn("校园营销海报设计",
              ["开学季主色用青绿+暖橙，突出活力",
               "主视觉放学生笑脸，避免堆砌文字",
               "行动号召用「限时立减」比「快来」转化高"],
              text="校园营销海报的视觉与文案要点。开学季场景下，情绪词比功能词有效。",
              src="自学")
        learn("数据结构与算法",
              ["栈是后进先出", "队列是先进先出"],
              text="基础数据结构。", src="自学")
        check("写入 2 条成功", len(K.search("", limit=99)) == 2,
              len(K.search("", limit=99)))

        hs = hits("给霖云智学做一份开学季招生海报")
        check("相关目标能命中", len(hs) >= 1, hs)
        check("命中的是营销那条而非算法那条",
              all("营销" in h["title"] or "海报" in h["title"] for h in hs), hs)
        check("命中项带可校验的 score", all(isinstance(h["score"], int) for h in hs),
              hs)

        # --- 3. 可证伪：无关目标不回流 ---
        check("无关目标（算法）不混入营销知识",
              all("营销" not in h["title"] for h in hits("快速排序的时间复杂度")),
              [h["title"] for h in hits("快速排序的时间复杂度")])
        check("完全无关 → 空", hits("今晚吃什么") == [],
              [h["title"] for h in hits("今晚吃什么")])

        # --- 4. 门限可调（证明"够分才回"这条规则真的在起作用） ---
        check("门限抬高到 999 → 一条都不回",
              hits("给霖云智学做一份开学季招生海报", min_score=999) == [])

        # --- 5. digest 内容可读、带来源提示 ---
        d = digest("给霖云智学做一份开学季招生海报")
        check("digest 含标题与要点", "校园营销海报设计" in d and "青绿" in d, d[:120])
        check("digest 带'学过'抬头", "你学过" in d)

        # --- 6. feed 把知识拼在提示词前面 ---
        f = feed("写三个标题", "做一份开学季招生海报")
        check("feed 含原提示词", "写三个标题" in f)
        check("feed 含知识", "校园营销海报设计" in f)
        check("feed 末尾提示别硬凑", "硬凑" in f)
        check("feed 比原提示词长", len(f) > len("写三个标题"))

        # --- 7. max_chars 截断（按整条砍） ---
        small = digest("给霖云智学做一份开学季招生海报", max_chars=60)
        check("max_chars 生效", len(small) <= max(60, len(small.split("\n")[0])),
              len(small))

        # --- 8. applied_note 可回显用了哪些知识 ---
        note = applied_note("给霖云智学做一份开学季招生海报")
        check("applied_note 带条数与分数", "分)" in note, note)

        # --- 9. learn 空标题不写 ---
        before = len(K.search("", limit=99))
        learn("   ", ["x"])
        check("空标题不写入", len(K.search("", limit=99)) == before)

        # --- 10. 坏库不抛（失败不阻塞工作流） ---
        with open(K.KNOW_FILE, "w", encoding="utf-8") as fp:
            fp.write("{这不是合法 json")
        K._KNOW_CACHE["ts"] = 0.0
        K._KNOW_CACHE["data"] = None
        try:
            hits("做海报")
            digest("做海报")
            ok = True
        except Exception as e:  # noqa: BLE001
            ok = False
            print("      异常：%s" % e)
        check("知识库损坏时返回空而不抛", ok)
    finally:
        K.KNOW_FILE, K.BOOKS_DIR = saved
        K._KNOW_CACHE["ts"] = 0.0
        K._KNOW_CACHE["data"] = None
        shutil.rmtree(tmp, ignore_errors=True)

    print("-" * 46)
    if fails:
        print("自检失败 %d/%d：%s" % (len(fails), n["v"], "；".join(fails)))
        return 1
    print("自检通过：%d 项全绿" % n["v"])
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_selftest())
