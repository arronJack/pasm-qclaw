"""knowledge v3 —— PASM 分层知识库：全文保真 + 要点索引 / 去重巩固 / 关联 / 交叉互补。

v3 升级点（v0.16）：
- 条目新增 text(原文全文) 与 src(来源：书名/网页/对话)，要点 bullets 只做"速记索引"，
  "学"不再等于"丢原文"——任何知识任何时候都能回取原文片段（snippet_for）。
- record 合并做 要点并集 + 全文保留较完整一份（其他内容不因提炼而被忽略）。

数据：DATA_DIR/knowledge.json
条目结构：
  {title, topic, date, cat, tags[], bullets[], text, src, related[title],
   conf 0-1, n_read}
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from typing import Callable, List, Optional, Tuple

def _resolve_data_dir() -> str:
    """统一数据目录（v0.30.5）—— 与 worklog / 日志同源，支持 PASM_STUDIO_DIR 覆盖。

    旧实现硬编码 `%APPDATA%/PASMStudio`，有两个真问题：
      ① 与 `logsetup.data_dir()` **不同源**。源码运行时 logsetup 指向
         `<repo>/.pasmstudio_dev`，于是同一台机器上「工作记录」和「知识库」
         落在两个目录里 —— 备份 / 迁移 / 排查必然漏掉一个。
      ② 不认 `PASM_STUDIO_DIR` → 跑测试或开第二个实例会直接写用户真实知识库。
    现在统一走 logsetup；**但若新目录下还没有知识库、而旧目录里有，就继续用旧的**，
    避免升级后用户看到"知识全没了"。
    """
    # 显式覆盖优先级最高（测试 / 多实例）—— 不做旧库回退，
    # 否则设了 PASM_STUDIO_DIR 也会被'旧库在旧处'这条规则吃掉。
    env_dir = os.environ.get("PASM_STUDIO_DIR")
    if env_dir:
        return env_dir
    olds = []
    if os.name == "nt":
        olds.append(os.path.join(os.environ.get("APPDATA") or
                                 os.path.expanduser("~"), "PASMStudio"))
    olds.append(os.path.expanduser("~/.pasmstudio"))
    try:
        import logsetup
        new = logsetup.data_dir()
    except Exception:  # noqa: BLE001  拿不到就退回旧行为
        return olds[0]
    if new in olds:
        return new
    if not os.path.exists(os.path.join(new, "knowledge.json")):
        for o in olds:
            if os.path.exists(os.path.join(o, "knowledge.json")):
                return o                      # 旧库仍在旧处 → 不搬家（不丢数据）
    return new


DATA_DIR = _resolve_data_dir()
BOOKS_DIR = os.path.join(DATA_DIR, "books")
KNOW_FILE = os.path.join(DATA_DIR, "knowledge.json")
os.makedirs(BOOKS_DIR, exist_ok=True)

TAXONOMY = {
    "安全": ["密码", "安全", "漏洞", "钓鱼", "备份", "隐私", "病毒", "勒索", "补丁", "防火墙"],
    "编程": ["python", "代码", "函数", "变量", "循环", "程序", "debug", "算法", "变量", "对象"],
    "效率": ["待办", "时间", "习惯", "清单", "专注", "效率", "计划", "目标"],
    "生活": ["吃饭", "休息", "睡觉", "锻炼", "喝水", "心情", "健康"],
    "AI": ["模型", "训练", "神经网络", "ai", "智能体", "学习率", "深度学习", "gpt"],
    "数据": ["数据", "数据库", "统计", "清洗", "表格", "sql"],
    "网络": ["网络", "http", "服务器", "网页", "接口", "dns", "url"],
    "娱乐": ["笑话", "段子", "脑筋急转弯", "谜语", "相声", "幽默", "故事", "古诗", "成语"],
    "通用": [],
}

_SEEDS = {
    "网络安全入门.md":
        "网络安全是保护计算机系统和数据不被窃取、篡改或破坏的学问。\n"
        "第一课：强密码。至少12位，混合大小写字母、数字和符号，不同网站不要用同一个密码。\n"
        "第二课：及时更新。系统和软件更新常常修补安全漏洞，不要长期忽略更新提示。\n"
        "第三课：识别钓鱼。陌生邮件里索要密码或让你点击可疑链接的，大多不是真的。\n"
        "第四课：备份数据。重要的文件至少做两份备份，一份放在离线位置。\n"
        "记住：安全的第一步，是习惯。",
    "Python 基础.md":
        "Python 是一种简单清晰的编程语言。\n"
        "变量用来存放数据：name = '小U'。\n"
        "if 用来做判断：if 温度 > 30: 就说好热。\n"
        "for 用来循环：for i in range(3): 做三次。\n"
        "函数用 def 定义，把一段逻辑包成可以重复调用的盒子。\n"
        "写程序最重要的是先想清楚，再动手。",
    "效率与待办方法.md":
        "好的工作习惯从待办清单开始。\n"
        "规则一：想到就记，不要靠脑子。\n"
        "规则二：给每件事一个时间，不然永远说'以后再做'。\n"
        "规则三：优先做最重要的一件事，而不是最容易的那件。\n"
        "规则四：完成了就划掉，给自己一点正反馈。",
}


_KNOW_CACHE = {"ts": 0.0, "data": None}


def _load_know() -> List[dict]:
    try:
        if os.path.exists(KNOW_FILE):
            now = time.time()
            c = _KNOW_CACHE
            if c["data"] is not None and (now - c["ts"]) < 2.0:
                return c["data"]
            try:
                d = json.load(open(KNOW_FILE, "r", encoding="utf-8"))
            except Exception:
                # v0.27.11：读失败（后台正在写/文件被截断）→ 退回上一次的好数据，
                # 绝不返回 []。否则这一轮聊天会静默丢掉整个知识库注入。
                return c["data"] if c["data"] is not None else []
            c["data"] = d
            c["ts"] = now
            return d
    except Exception:
        pass
    return []


def _save_know(know: List[dict]):
    """原子写：先写 .tmp 再 os.replace —— 后台自学写入的瞬间，聊天侧不会读到半截 JSON。"""
    try:
        tmp = KNOW_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(know, f, ensure_ascii=False, indent=1)
        os.replace(tmp, KNOW_FILE)          # Windows/POSIX 均为原子替换
        _KNOW_CACHE["data"] = know
        _KNOW_CACHE["ts"] = time.time()
    except Exception:
        pass


# ---------------- 书库（v0.27.8 起统一为 .md） ----------------
def ensure_seed_books() -> List[str]:
    names = []
    for fn, content in _SEEDS.items():
        p = os.path.join(BOOKS_DIR, fn)
        if not os.path.exists(p):
            with open(p, "w", encoding="utf-8") as f:
                f.write("# " + fn[:-3] + "\n\n" + content)
        names.append(fn)
    # 兼容迁移：旧版 .txt 资料库 → 转成 .md（标题统一、便于资料库阅读）
    try:
        for fn in os.listdir(BOOKS_DIR):
            if fn.endswith(".txt"):
                stem = fn[:-4]
                md = stem + ".md"
                if not os.path.exists(os.path.join(BOOKS_DIR, md)):
                    try:
                        with open(os.path.join(BOOKS_DIR, fn), "r",
                                  encoding="utf-8", errors="replace") as f:
                            data = f.read()
                        if not data.lstrip().startswith("#"):
                            data = "# " + stem + "\n\n" + data
                        with open(os.path.join(BOOKS_DIR, md), "w",
                                  encoding="utf-8") as f:
                            f.write(data)
                    except Exception:
                        continue
                try:
                    os.remove(os.path.join(BOOKS_DIR, fn))
                except Exception:
                    pass
    except Exception:
        pass
    return names


def list_books() -> List[str]:
    ensure_seed_books()
    files = [n for n in os.listdir(BOOKS_DIR) if n.endswith(".md")]
    # 兜底：若仍有未迁移的 .txt（迁移失败），补上且不重复同 stem
    stems = {n[:-3] for n in files}
    for n in os.listdir(BOOKS_DIR):
        if n.endswith(".txt") and n[:-4] not in stems:
            files.append(n)
    return sorted(files)


def read_book(title: str) -> str:
    ensure_seed_books()
    # title 可能带 .txt/.md 或无扩展名；优先 .md，回退 .txt
    base = os.path.join(BOOKS_DIR, title)
    if os.path.exists(base):
        try:
            return open(base, "r", encoding="utf-8").read()
        except Exception:
            return title
    if title.endswith(".txt"):
        stem = title[:-4]
    elif title.endswith(".md"):
        stem = title[:-3]
    else:
        stem = title
    for ext in (".md", ".txt"):
        p = os.path.join(BOOKS_DIR, stem + ext)
        if os.path.exists(p):
            try:
                return open(p, "r", encoding="utf-8").read()
            except Exception:
                return title
    return title


def _clean_title(title: str) -> str:
    return re.sub(r"\.(txt|md)$", "", title).strip()


def stem(name: str) -> str:
    """书名/文件名 → 去扩展名（.md/.txt）的统一标题。

    用于与 learned_titles() 比对「这本书学过没」——v0.27.8 起书库统一 .md，
    任何仍按 .txt 长度截断（b[:-4]）的旧写法都会导致「永远判为新书 → 反复重读」。
    """
    return _clean_title(name or "")


# ---------------- 分类 ----------------
def classify(title: str, content: str = "") -> Tuple[str, List[str]]:
    low = (title + " " + content).lower()
    scores = {}
    for cat, kws in TAXONOMY.items():
        if not kws:
            continue
        scores[cat] = sum(1 for k in kws if k in low)
    cat = max(scores, key=scores.get) if scores and max(scores.values()) > 0 else "通用"
    tags = [t for t in re.findall(r"[A-Za-z]{3,}|[\u4e00-\u9fa5]{2,4}", low)
            if t in sum(TAXONOMY.values(), [])]
    tags = list(dict.fromkeys(tags[:6]))
    if not tags:
        tags = [cat]
    return cat, tags


# ---------------- 学习记录：新增 / 巩固 / 关联 ----------------
def _overlap_score(t1: List[str], t2: List[str], title1: str, title2: str) -> float:
    s = len(set(t1) & set(t2)) * 2
    for w in t1:
        if w and w in title2:
            s += 1
    for w in t2:
        if w and w in title1:
            s += 1
    return s


def _refresh_links(know: List[dict]):
    for e in know:
        rel = []
        for o in know:
            if o is e or o["title"] == e["title"]:
                continue
            sc = _overlap_score(e.get("tags", []), o.get("tags", []),
                                e["title"], o["title"])
            if sc >= 1:
                rel.append((sc, o["title"]))
        rel.sort(reverse=True)
        e["related"] = [t for _, t in rel[:2]]


def record(title: str, bullets: List[str], topic: str = "",
           text: str = "", src: str = "") -> int:
    """新增知识；同题重学=巩固（要点并集、全文保较完整一份、置信度上升、时间刷新）。

    text：可回取的原文全文（要点之外的细节不再丢失）；src：来源（书名/网页/对话/用户分享）。
    """
    know = _load_know()
    ct = _clean_title(title)
    cat, tags = classify(title, " ".join(bullets))
    now = time.strftime("%Y-%m-%d %H:%M")
    for e in know:
        if _clean_title(e["title"]) == ct or e["title"] == title:
            old = e.get("bullets", [])
            merged = list(dict.fromkeys(old + bullets))[:10]
            # 全文并集：保留更长/更新的一份，绝不因提炼要点而把原文弄丢
            old_t = e.get("text", "") or ""
            if len(text) > len(old_t):
                merged_t = text[:40000]
            else:
                merged_t = old_t[:40000]
            e.update(bullets=merged, date=now,
                     conf=min(1.0, e.get("conf", 0.6) + 0.15),
                     n_read=e.get("n_read", 1) + 1, cat=cat or e.get("cat", "通用"),
                     tags=tags or e.get("tags", []))
            if merged_t:
                e["text"] = merged_t
            if src and (not e.get("src") or src not in e.get("src", "")):
                e["src"] = src
            _refresh_links(know)
            _save_know(know)
            return len(know)
    know.append({"title": ct, "topic": topic or ct,
                 "date": now, "cat": cat,
                 "tags": tags, "bullets": bullets[:10],
                 "text": (text or "")[:40000], "src": src,
                 "related": [],
                 "conf": 0.6, "n_read": 1})
    _refresh_links(know)
    _save_know(know)
    return len(know)


def cross_notes(query: str | None = None, limit: int = 2) -> str:
    """交叉互补串讲：找出有关联的相邻知识，生成“联想笔记”。

    v0.28.1 前：完全无视 query，任何关联型问题都塞固定两条联想 —— 纯噪音 + 浪费预填充。
    现在：query 非空时，只回**与 query 语义相关**的关联（用 memvec 余弦相似度过滤），
    与问题八竿子打不着的联想不再进提示词。
    """
    know = _load_know()
    lines = []
    for e in know:
        for r in e.get("related", []):
            pair = f"{e['title']} ↔ {r}"
            if pair not in lines and (r + " ↔ " + e["title"]) not in lines:
                lines.append(pair)
    if not lines:
        return ""
    # query 相关度过滤：只保留与当前问题语义相近的关联
    if query:
        try:
            import memvec
            qv = memvec.embed(query)
            scored = []
            for pair in lines:
                a, b = pair.split(" ↔ ")
                ea = next((x for x in know if x["title"] == a), None)
                eb = next((x for x in know if x["title"] == b), None)
                tags = (ea or {}).get("tags", []) + (eb or {}).get("tags", [])
                text = " ".join(tags) + " " + a + " " + b
                sim = memvec.cosine(qv, memvec.embed(text))
                if sim >= 0.15:
                    scored.append((sim, pair))
            scored.sort(reverse=True)
            lines = [p for _, p in scored[:limit]]
        except Exception:
            lines = lines[:limit]
    else:
        lines = lines[:limit]
    out = []
    for pair in lines:
        a, b = pair.split(" ↔ ")
        ea = next((x for x in know if x["title"] == a), None)
        eb = next((x for x in know if x["title"] == b), None)
        if ea and eb:
            ta = ea.get("tags", [])[:2]
            tb = eb.get("tags", [])[:2]
            out.append(f"· 联想：学《{a}》时想到《{b}》——"
                       f"它们都在聊{('、'.join(list(set(ta + tb))[:3]) or '相近')}，"
                       f"可以放在一起用")
        else:
            out.append(f"· 联想：{a} ↔ {b}")
    return "\n".join(out)


def related_of(title: str) -> List[str]:
    know = _load_know()
    for e in know:
        if e["title"] == _clean_title(title):
            return e.get("related", [])
    return []


def _kw_of(text: str) -> List[str]:
    """把一段话拆成可用于原文检索的关键词（中文2-6字/英文≥3）。"""
    return list(dict.fromkeys(
        w for w in re.findall(r"[\u4e00-\u9fa5]{2,6}|[A-Za-z][A-Za-z0-9_\-]{2,}",
                              text or "") if len(w) >= 2))[:8]


# 常见功能词：2 字滑窗会产生大量噪声，不参与相关性打分
_STOP_GRAMS = {
    "什么", "怎么", "为什", "可以", "如何", "怎样", "知道", "告诉", "帮我", "一下",
    "一个", "这个", "那个", "我们", "你们", "他们", "就是", "不是", "没有", "还有",
    "应该", "需要", "问题", "方法", "时候", "现在", "如果", "因为", "所以", "但是",
    "而且", "然后", "这些", "那些", "一些", "有点", "真的", "觉得", "感觉", "想要",
    "为什么", "怎么办", "是什么", "可不可", "能不能", "行不行",
}
_KB_MIN_SCORE = 3          # 相关性下限：够分才注入正文（避免塞无关内容干扰聊天）


def _pick_even(seq: List[str], k: int) -> List[str]:
    """跨整段均匀取 k 个 —— 防止只取到句首碎片（v0.30.5）。"""
    if k <= 0 or not seq:
        return []
    if len(seq) <= k:
        return list(seq)
    step = len(seq) / float(k)
    return [seq[min(len(seq) - 1, int(i * step))] for i in range(k)]


def _query_grams(text: str, maxn: int = 30) -> List[str]:
    """把用户问句切成可匹配的词元：中文 2~4 字滑窗 + 英文/数字词。

    v0.27.11：旧写法用「最长 6 字连续段」当整词（例："待办清单怎么做" 整块拿去匹配），
    自然问句几乎匹配不到任何条目 —— **自学成果明明在库里却用不上**。

    v0.30.5 再修：改成"长词优先 + 末尾整体截断"后又出新问题 —— **2 字核心名词被饿死**。
    实测「给霖云智学做一份开学季招生海报」切出的 30 个词元里，2 字词只有
    '给霖/霖云/云智/智学/学做'（全是产品名碎片），真正的名词「海报」「招生」
    「开学」一个都没进来 → 库里明明有《校园营销海报设计》，打分只有 2 分（门限 3），
    聊天注入与工作流回流**双双召回不到**。
    现在按档位配额抽取（2 字给最多，因为中文核心名词多为 2 字），
    且每档**跨整句均匀取样**，句尾的名词不会被句首长词挤掉。
    """
    runs = re.findall(r"[\u4e00-\u9fa5]+", text or "")
    buckets: dict = {4: [], 3: [], 2: []}
    for run in runs:
        n = len(run)
        if n <= 4:
            if n >= 2:
                buckets[n].append(run)       # 本身就是短词，直接进对应档
            continue
        for w in (4, 3, 2):
            for i in range(n - w + 1):
                g = run[i:i + w]
                if w == 2 and g in _STOP_GRAMS:
                    continue
                buckets[w].append(g)
    quota = {4: maxn // 5, 3: maxn // 4,
             2: maxn - maxn // 5 - maxn // 4}      # 30 → 6 / 7 / 17
    out: List[str] = []
    for w in (2, 4, 3):                  # 先取 2 字：它最可能命中标题，别被截断吃掉
        out += _pick_even(list(dict.fromkeys(buckets[w])), quota[w])
    out += re.findall(r"[A-Za-z][A-Za-z0-9_\-]{2,}", text or "")
    return list(dict.fromkeys(out))[:maxn]


def _gram_weight(g: str) -> int:
    """匹配权重：英文/数字词 3，中文 3~4 字 2，中文 2 字 1。"""
    if g and ord(g[0]) < 128:
        return 3
    return 2 if len(g) >= 3 else 1


def _kb_score(e: dict, low: str, grams: List[str]) -> int:
    """条目与问句的相关性打分（标题局部命中权重最高）。"""
    if not low or not grams:
        return 0
    s = 0
    struct = (e["title"] + " " + " ".join(e.get("tags", [])) + " " +
              " ".join(e.get("bullets", []))).lower()
    hay = struct + " " + (e.get("text", "") or "")[:1500].lower()
    for g in grams:
        gl = g.lower()
        if gl not in hay:
            continue
        w = _gram_weight(g)
        # v0.30.5：命中标题/标签/要点 = 强信号（要点是提炼过的，撞词概率低），
        # 低权重词元抬到门限；只命中正文的仍按原权重（防长正文里撞词误召回）。
        if gl in struct and w < _KB_MIN_SCORE:
            w = _KB_MIN_SCORE
        s += w
    title = (e.get("title") or "").lower()
    if title and title in low:
        s += 6
    else:
        for g in grams:                      # "天文" → 命中标题《天文科普》
            if len(g) >= 2 and g.lower() in title:
                s += 8
                break
    for tag in e.get("tags", []):
        if tag and tag.lower() in low:
            s += 4
    return s


def _entry_snippet(e: dict, query: str, maxn: int = 1) -> str:
    """从该条原文里挑含关键词的真实句子 —— 要点之外的细节照样能回取。"""
    raw = e.get("text", "") or ""
    if not raw or len(raw) < 30:
        return ""
    # v0.27.11：摘录用「较长词元」挑句（2 字滑窗几乎每句都中，会挑出无关句）
    words = [g for g in _query_grams(query, 30)
             if len(g) >= 3 or (g and ord(g[0]) < 128)] or _kw_of(query)
    if not words:
        return ""
    sents = [s.strip() for s in re.split(r"(?<=[。！？!?；;])", raw) if len(s.strip()) >= 10]
    hits = [s for s in sents if any(w.lower() in s.lower() for w in words)]
    if not hits:
        return ""
    pick = hits[:maxn]
    seg = " ".join((s[:130] + "…") if len(s) > 130 else s for s in pick)
    return "·〔原文摘录〕" + seg


def _entry_head(e: dict, max_bullets: int = 3) -> str:
    head = e["title"]
    if e.get("cat") and e["cat"] != "通用":
        head += f"[{e['cat']}]"
    body = "；".join(e.get("bullets", [])[:max_bullets])
    return f"· 《{head}》—— " + (body or "（暂无要点，可回取原文）")


def learned_bullets(limit: int = 6) -> str:
    """注入文本：最近的几条知识 + 一跳邻居 + 交叉联想。"""
    know = _load_know()
    if not know:
        return ""
    know = sorted(know, key=lambda x: x.get("date", ""), reverse=True)
    chosen, seen = [], set()
    for e in know:
        chosen.append(e)
        seen.add(e["title"])
        if len(chosen) >= 2:
            break
    for e in list(chosen):
        for r in e.get("related", []):
            nxt = next((x for x in know if x["title"] == r), None)
            if nxt and r not in seen:
                chosen.append(nxt)
                seen.add(r)
            if len(chosen) >= limit:
                break
        if len(chosen) >= limit:
            break
    lines = [_entry_head(e) for e in chosen[:limit]]
    cross = cross_notes(2)
    if cross:
        lines.append("【交叉联想】\n" + cross)
    return "\n".join(lines)


def learned_titles() -> List[str]:
    return [e["title"] for e in _load_know()]


def inject_relevant(text: str, limit: int = 6) -> str:
    """按用户消息相关性挑选自学知识注入 → 学过什么聊什么就更专业。

    v3：命中条目除速记要点外，再从该条原文里摘录真实句子（学过的细节不会丢）。
    """
    know = _load_know()
    if not know:
        return ""
    low = (text or "").lower()

    def score(e: dict) -> int:
        if not low:
            return 0
        s = 0
        hay = (e["title"] + " " + " ".join(e.get("tags", [])) + " " +
               " ".join(e.get("bullets", []))).lower()
        for tag in e.get("tags", []):
            if tag and tag.lower() in low:
                s += 4
        if e["title"].lower() in low:
            s += 6
        for w in re.findall(r"[\u4e00-\u9fa5]{2,4}|[A-Za-z]{3,}", low):
            if len(w) >= 2 and w in hay:
                s += 2
        return s

    ranked = sorted(know, key=score, reverse=True)
    chosen = [e for e in ranked if score(e) > 0][:limit]
    if not chosen:                       # 没有相关的 → 退化为最近学的几条
        chosen = sorted(know, key=lambda x: x.get("date", ""), reverse=True)[:limit]
    if not chosen:
        return ""
    lines = []
    for e in chosen:
        lines.append(_entry_head(e, max_bullets=3))
        sn = _entry_snippet(e, text)
        if sn:
            lines.append(sn)
    cross = cross_notes(2)
    if cross:
        lines.append("【交叉联想】\n" + cross)
    return "\n".join(lines)


def inject_for_chat(text: str, limit: int = 3, excerpt: int = 260) -> str:
    """聊天侧知识注入（v0.27.11）——「先吸收分析，再智能给出」。

    与 inject_relevant 的三点不同（都是为了让自学成果**真正用得上**、又不干扰聊天）：
    1. **不相关就不注入**：旧版匹配不到时会退化塞"最近学的 6 条"，既干扰回答又拖慢生成；
       这里命中为空只给一行"已学主题清单"（便于它诚实说明学没学过），不再塞正文。
    2. **给足可分析的料**：命中条目除要点外，从原文里摘最多 2 句真实语句。
    3. **带"分析指令"**：要求先做逻辑分析 + 思维模拟，再用自己的话给结论——
       不整段照搬、不罗列要点、不说"根据我学过的资料"这类空话。

    返回空串表示"这次没什么可给的"，调用方跳过即可。
    """
    know = _load_know()
    t = (text or "").strip()
    if not know or not t:
        return ""
    low = t.lower()
    grams = _query_grams(t)
    scored = sorted(((_kb_score(e, low, grams), e) for e in know),
                    key=lambda p: p[0], reverse=True)
    chosen = [e for sc, e in scored if sc >= _KB_MIN_SCORE][:limit]
    if not chosen:
        # 没相关的 → 只给一份"我学过什么"的清单（很省 token），不塞无关正文
        recent = sorted(know, key=lambda x: x.get("date", ""), reverse=True)[:8]
        names = [e["title"] for e in recent if e.get("title")]
        if not names:
            return ""
        return ("【你自学过的主题】" + "、".join(names) +
                "。（只在与当前问题相关时提一句你学过；无关就别牵扯这些内容）")
    lines: List[str] = []
    for e in chosen:
        head = e["title"]
        if e.get("cat") and e["cat"] != "通用":
            head += "[" + e["cat"] + "]"
        lines.append("《" + head + "》（" + (e.get("src") or "自学") + "）")
        bl = [str(b).strip() for b in (e.get("bullets") or [])[:4] if str(b).strip()]
        if bl:
            lines.append("  · 我记下的要点：" + "；".join(b[:80] for b in bl))
        sn = _entry_snippet(e, t, maxn=2)
        if sn:
            lines.append("  " + sn)
        else:
            body = (e.get("text") or "").strip()
            if body:
                lines.append("  ·〔原文摘录〕" + body[:excerpt].replace("\n", " "))
        lines.append("")
    titles = "、".join("《" + e["title"] + "》" for e in chosen)
    head = (
        "【你自学过的相关资料·" + titles + "】上面是你在资料库里真正学过的内容。"
        "回答前请先在脑子里做两件事：① 逻辑分析——把资料里的因果、步骤、前提条件、"
        "适用边界理清楚；② 思维模拟——把结论代进用户的具体情形跑一遍，看是否成立、有没有例外。"
        "然后用你自己的话把结论和理由讲出来（口语化、直击用户的问题；可以引用具体细节和数字，"
        "但不要整段照搬原文、不要罗列“要点清单”、不要说“根据我学过的资料”这类空话）。"
        "资料只覆盖一部分就明说是哪部分、剩下的靠常识推断；资料其实不相关就别硬扯。"
    )
    return "\n".join([head] + lines).strip()


def search_snippets(query: str, limit: int = 2) -> List[dict]:
    """公开检索：返回与 query 相关条目的原文片段（记忆/资料库面板可用）。"""
    out = []
    for e in inject_relevant_query(query, limit):
        sn = _entry_snippet(e, query, maxn=2) or ""
        out.append({"title": e["title"], "src": e.get("src", ""),
                    "bullets": e.get("bullets", [])[:3], "snippet": sn,
                    "conf": e.get("conf", 0.0), "n_read": e.get("n_read", 0)})
    return out


def inject_relevant_query(query: str, limit: int = 3) -> List[dict]:
    """按相关性返回条目对象列表（供面板/自测使用）。v0.27.11：改用词元滑窗匹配。"""
    know = _load_know()
    low = (query or "").lower()
    grams = _query_grams(query or "")

    def score(e: dict) -> int:
        return _kb_score(e, low, grams)

    if not low:
        return sorted(know, key=lambda x: x.get("date", ""), reverse=True)[:limit]
    ranked = sorted(know, key=score, reverse=True)
    return [e for e in ranked if score(e) > 0][:limit] or \
        sorted(know, key=lambda x: x.get("date", ""), reverse=True)[:limit]


def find_entry(title: str) -> Optional[dict]:
    """按标题查一条知识（资料库全文阅读/书架回填用），找不到返回 None。"""
    ct = _clean_title(title or "")
    for e in _load_know():
        if _clean_title(e["title"]) == ct or e["title"] == (title or ""):
            return e
    return None


def entries() -> List[dict]:
    """全部知识条目（资料库面板用）。"""
    return _load_know()


# ---------------- 文本提炼（保留原接口） ----------------
def rule_bullets(content: str, maxn: int = 5) -> List[str]:
    out = []
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        line = line.strip("。，；： ")
        if 6 <= len(line) <= 60 and line not in out:
            out.append(line)
        if len(out) >= maxn:
            break
    return out


def sentence_bullets(content: str, maxn: int = 6) -> List[str]:
    """无模型兜底：按句子切分取有信息量的短句（网页正文常被压成超长单行）。"""
    if not content:
        return []
    sents = [s.strip(" \t\n。，；：") for s in re.split(r"(?<=[。！？!?；;])", content)
             if len(s.strip(" \t\n。，；：")) >= 8]
    out = []
    for s in sents:
        if len(s) <= 90 and s not in out:
            out.append(s)
        if len(out) >= maxn:
            break
    if not out:                       # 都是长句 → 掐头取片
        for s in sents[:maxn]:
            out.append(s[:90] + "…")
    return out[:maxn]


def summarize(title: str, content: str,
              llm: Optional[tuple] = None,
              log: Optional[Callable[[str], None]] = None) -> List[str]:
    if llm:
        try:
            import llm_gateway as GW
            ans = GW.gw.complete(
                llm[0], llm[1], "local",
                [
                    {"role": "system",
                     "content": "你是爱学习的小机器人。把书里最重要的内容提炼成 3-5 条"
                                "中文要点，每行一条，不要其他装饰。"},
                    {"role": "user", "content": content[:1500]}],
                task="study", temperature=0.3, max_tokens=300)
            bullets = [ln.strip("•-* 0123456789.、 ") for ln in (ans or "").splitlines()
                       if ln.strip()]
            if len(bullets) >= 2:
                return bullets[:5]
        except Exception as ex:
            if log:
                log(f"summarize llm fail: {ex}")
    return rule_bullets(content)


# ==================================================================
# v0.22.0 学以致用 + Obsidian 式检索
# ------------------------------------------------------------------
# 使用动词后跟的"对象词"→ 去知识库条目里找学过的对应内容，把原文真正带进提示词
# ——解决"自学了笑话大全，让它讲笑话却讲不出来"：以前只注入要点摘要，
#   现在命中"应用意图"时把学到的原文整段带出来，让模型直接用。
_RE_USE_VERB_OBJ = re.compile(
    r"(?:讲|说|来|表演|演示|教我|教教|举例?|背|唱|念|出|放)"
    r"(?:一?[个段则首篇句条张]?)?"
    r"([\u4e00-\u9fa5A-Za-z]{2,8})")


# 表演/复现型内容：需要"照原样"才有价值（笑话要完整才响、谜语要原样才成立）
_PERF_KEYS = ("笑话", "冷笑话", "段子", "脑筋急转弯", "谜语", "绕口令", "歇后语",
              "古诗", "成语故事", "寓言", "童话")


def inject_applicable(text: str, limit: int = 2, excerpt: int = 600) -> str:
    """检测"用本事"请求（讲个笑话/给我讲讲xx/举例…）→ 命中自学条目时注入。

    双模式（v0.22.1 修复"照搬资料库"）：
    · 表演型内容（笑话/段子/谜语…）：注入原文片段，指令"照原样表演"——完整才有包袱；
    · 学科/知识型内容（心理学/编程…）：只注入要点提示，指令"用自己的话给朋友讲，
      简明扼要、不超过 300 字、不要复述要点原文"——避免整段照抄资料库。
    """
    know = _load_know()
    t = (text or "").strip()
    if not know or not t:
        return ""
    objs = []
    for m in _RE_USE_VERB_OBJ.finditer(t):
        o = (m.group(1) or "").strip()
        if o and o not in objs:
            objs.append(o)
    if not objs:
        return ""
    hits: List[Tuple[dict, str]] = []
    for e in know:
        hay = (e["title"] + " " + " ".join(e.get("tags", [])) + " " +
               " ".join(e.get("bullets", []))).lower()
        for o in objs:
            ol = o.lower()
            if len(ol) < 2:
                continue
            if ol in hay:
                hits.append((e, o))
                break
            # 2字滑窗容错（"冷笑话" vs "笑话"）
            gs = {ol[i:i + 2] for i in range(len(ol) - 1)}
            if any(g in hay for g in gs if len(g) >= 2):
                hits.append((e, o))
                break
    if not hits:
        return ""

    def _is_perf(e: dict) -> bool:
        hay = (e["title"] + " " + " ".join(e.get("tags", []))).lower()
        return any(k in hay for k in _PERF_KEYS)

    lines = []
    perf_done = False
    seen_titles = set()
    for e, _o in hits[:limit]:
        if e["title"] in seen_titles:
            continue
        seen_titles.add(e["title"])
        if _is_perf(e):
            perf_done = True
            lines.append(f"《{e['title']}》原文（挑最合适的讲出来，铺垫和包袱都要完整）：")
            body = (e.get("text") or "").strip()
            if body:
                lines.append(body[:excerpt])
            else:
                lines.append("\n".join("· " + b for b in e.get("bullets", [])[:6]))
        else:
            lines.append(f"《{e['title']}》要点参考（先逻辑分析、做一遍思维模拟再作答；"
                         f"用自己的话讲，别列要点清单、别照搬）：")
            bl = [str(b)[:80] for b in (e.get("bullets", [])[:5])]
            if bl:
                lines.append("；".join(b for b in bl))
            else:
                lines.append("（该条只有原文，先想清楚再用自己的话概括 2-3 句）")
        lines.append("")
    head = ("【用出你的本事】上面是你真正学过的内容——现在把学到的用出来，"
            "直接满足用户刚才的请求。"
            + ("要像在朋友面前表演一样，把选中的内容完整讲出来（口语化、有现场感）。"
               if perf_done else
               "先对自己学到的东西做一遍逻辑分析、在脑中跑一遍思维模拟"
               "（想清楚因果 / 步骤 / 适用边界），再结合你自己的理解组织答案——"
               "口语化、通俗、简明，正文不超过 300 字；只引用关键要点，绝不大段照搬、"
               "整段复述资料库原文，绝不把'要点/原文'当答案甩给用户。"))
    return "\n".join([head] + lines).strip()


def pretty_text(raw: str, width: int = 44) -> str:
    """把学回来的原文整理成"能读的排版"（v0.22.1 资料库排版修复）。

    - 压掉连续空行（>1 行→1 行）；
    - 丢掉纯符号/极短噪声行；
    - 超长无断句的行，就近在 。！？；， 处折行，避免"一整行几百字挤成一团"；
    - 保留 markdown 结构行（-、#、数字、>、```）原样。
    """
    if not raw:
        return raw
    out = []
    for line in (raw or "").replace("\r\n", "\n").split("\n"):
        s = line.rstrip()
        st = s.strip()
        if not st:
            if out and out[-1] != "":
                out.append("")
            continue
        if len(st) <= 2 and not re.search(r"[一-龥A-Za-z0-9]", st):
            continue
        if re.match(r"^([-*•] |#+\s|\d+[.、)]|\s*>|```|!\[|\|)", st):
            out.append(s)
            continue
        if len(st) > width:
            segs = re.split(r"(?<=[。！？；;])", st)
            piece = ""
            for seg in segs:
                if seg and len(piece) + len(seg) > width:
                    if piece:
                        out.append(piece.strip())
                    piece = seg
                else:
                    piece += seg
            if piece.strip():
                out.append(piece.strip())
            # 无标点超长句兜底：优先在标点处断；到宽度上限也断（≤width 字符/行）
            tail = out[-1] if out else piece
            if len(tail) > width:
                out[-1] = ""
                cur = ""
                for ch in tail:
                    cur += ch
                    if len(cur) >= width:
                        if ch not in "，。、！？；： \u3000" and len(cur) < width + 6:
                            continue        # 快到界了再等一个标点，不硬切单词/短句
                        out.append(cur)
                        cur = ""
                if cur:
                    out.append(cur)
        else:
            out.append(s)
    txt = "\n".join(out)
    txt = re.sub(r"\n{3,}", "\n\n", txt)
    return txt.strip()


def search(query: str, limit: int = 8) -> List[dict]:
    """Obsidian 式全文检索：标题/标签/要点/原文加权排序，带原文摘录与双链关联。"""
    know = _load_know()
    q = (query or "").strip()
    if not know:
        return []
    if not q:
        return [dict(e, snippet="") for e in
                sorted(know, key=lambda x: x.get("date", ""), reverse=True)[:limit]]
    ql = q.lower()

    def sc(e: dict) -> int:
        s = 0
        title = e["title"].lower()
        tags = " ".join(e.get("tags", [])).lower()
        bullets = " ".join(e.get("bullets", [])).lower()
        body = (e.get("text", "") or "").lower()
        if q in title:
            s += 10
        if q in tags:
            s += 6
        if q in bullets:
            s += 4
        if q in body:
            s += 2 + min(body.count(q), 5)
        for w in _kw_of(q):
            wl = w.lower()
            if wl and wl != q:
                if wl in title:
                    s += 4
                if wl in tags or wl in bullets:
                    s += 2
                if wl in body:
                    s += 1
        return s

    ranked = sorted(know, key=sc, reverse=True)
    out = []
    for e in ranked[:limit]:
        if sc(e) <= 0:
            break
        out.append(dict(e, snippet=_entry_snippet(e, q, maxn=2)))
    return out


# ======================================================================
# v0.30.8 资料库（Obsidian 风格）：把知识落成真正的 .md 笔记
# ======================================================================
# 为什么要这一层：知识本来就在 knowledge.json 里（连全文都有），但**给用户看的
# 只有 bullets 要点** —— "达不到资料库的作用"说的就是这件事。Obsidian 的形态
# 恰好是"一堆 .md + frontmatter + [[双链]] + 一个目录页"：
#   · 用户可以直接用 Obsidian「打开文件夹作为库」浏览/搜索/画关系图；
#   · 也可以完全不用 Obsidian —— 就是普通 Markdown，任何编辑器都能读。
_VAULT_NAME = "vault"
_VAULT_INDEX = "index.md"

# 网页噪声：这些行进了库只会把"资料"变成"目录页"
_WEB_NOISE = re.compile(
    r"^(首页|登录|注册|下载|APP|应用|广告|更多|展开|收起|目录|导航|编辑|讨论|"
    r"相关搜索|相关推荐|猜你喜欢|热门|推荐阅读|上一页|下一页|返回|顶部|底部|"
    r"百度百科|维基百科|搜狗|必应|意见反馈|免责声明|隐私政策|用户协议|"
    r"版权所有|Copyright|All Rights Reserved|分享到|扫一扫|关注我们|"
    r"\d+\s*条评论|查看全部|点击查看更多)\.?$", re.I)
_WEB_NOISE_SUB = re.compile(
    r"(登录|注册|下载APP|打开APP|广告|京ICP备|京公网安备|增值电信|"
    r"举报电话|违法和不良信息|网络文化经营许可证)")

JUNK_LINE = re.compile(
    "^[" + re.escape("".join([
        " ", "\t", "-", "—", "–", "·", "•", "。", "，", "、", ",", ".",
        "!", "！", "?", "？", ":", "：", ";", "；", "(", ")", "（", "）",
        "[", "]", "【", "】", chr(34), "'", "“", "”", "‘", "’", "/", "|",
        chr(92),
    ])) + "]+$")


def strip_web_noise(text: str) -> str:
    """清洗网页噪声：导航 / 广告 / 超短行 / 纯符号行 / 重复行。

    判据刻意保守 —— **只删明显不是内容的行**，绝不"总结"：
    资料库的价值在于**原文可得**，任何一步"帮你精简"都是在减少信息。
    """
    out, seen = [], set()
    for raw in (text or "").splitlines():
        ln = raw.strip()
        if not ln:
            out.append("")
            continue
        if len(ln) < 6:                       # 超短行（"更多""首页""1"）不是内容
            continue
        if JUNK_LINE.match(ln):
            continue
        if _WEB_NOISE.match(ln) or _WEB_NOISE_SUB.search(ln):
            continue
        if len(ln) <= 120 and ln in seen:
            # 重复行 = 导航 / 标签 / 站点样板 / 成段重复的摘要 —— 删。
            # 阈值放到 120 字：实测网页里"成段重复"的样板文字普遍 30~120 字，
            # 原来卡在 30 字漏掉了一大片（正是"资料很杂"的来源之一）。
            # 更长的重复行**保留**：正文整段逐字重复极少，而诗歌/歌词这类刻意重复，
            # 宁可少删不可多删。
            continue
        seen.add(ln)
        out.append(ln)
    joined = "\n".join(out)
    joined = re.sub(r"\n{3,}", "\n\n", joined)   # 压缩空行
    return joined.strip()


def vault_dir() -> str:
    """资料库目录（`.md` 笔记所在）。与 knowledge.json 同源，随 DATA_DIR 走。"""
    return os.path.join(DATA_DIR, _VAULT_NAME)


#: 打开文件夹的实现（可注入；测试替换它，**不要**去 patch 全局 `os`）。
#: 为什么需要这个缝：`os.startfile` **不受 `QT_QPA_PLATFORM=offscreen` 影响**
#: （离屏只管 Qt，那走的是 Win32 ShellExecute）—— 测试里会真弹一个资源管理器窗口，
#: 窗口指着的目录若随后被 rmtree，Windows 就弹「位置不可用」。2026-09-16 真踩到。
_opener = None


def _safe_filename(title: str) -> str:
    """标题 → 安全文件名（Windows 禁 9 个字符；再长的截断，避免路径超长）。"""
    t = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", str(title or "").strip())
    t = re.sub(r"\s+", " ", t).strip(" .")
    return (t[:80] or "未命名")


def _yaml_str(v) -> str:
    """frontmatter 里的字符串：统一双引号 + 转义，避免冒号/引号把 YAML 弄坏。"""
    s = str(v or "").replace("\\", "\\\\").replace('"', '\\"')
    return '"' + s.replace("\n", " ")[:400] + '"'


def _title_of(e: dict) -> str:
    """条目标题：`record()` 也接受文件名（如 `网络安全入门.md`），这里统一清掉后缀。"""
    t = str(e.get("title") or "").strip()
    for suf in (".md", ".markdown", ".txt"):
        if t.lower().endswith(suf):
            t = t[: -len(suf)]
    return t.strip() or "未命名"


def note_markdown(e: dict) -> str:
    """一条知识 → Obsidian 风格 Markdown 笔记（frontmatter + 结构 + 双链）。

    结构固定成这样，是为了**既给人读、也给 Obsidian 索引**：
      frontmatter（title/tags/category/source/conf/reads/日期）
      # 标题
      > [!note] 概要        ← Obsidian callout，一眼看到是什么
      ## 要点               ← 速记索引
      ## 全文               ← **资料库的本体**（清洗后的原文）
      ## 相关               ← [[双链]]
      ## 来源               ← URL / 书名，可追溯
    """
    title = _title_of(e)
    tags = [str(t).strip() for t in (e.get("tags") or []) if str(t).strip()]
    cat = str(e.get("cat") or "通用").strip() or "通用"
    if cat not in tags:
        tags.insert(0, cat)
    bullets = [str(b).strip() for b in (e.get("bullets") or []) if str(b).strip()]
    # v0.30.8：要点清洗（渲染期做，**不动用户已存的数据**）。
    # 真实取证：23 条里 18 条的**第一个要点就是标题本身**（`# Python 基础`）——
    # callout 于是变成「> [!note] # Python 基础」（等于没说），要点第一行也是废话；
    # 而且 `# xxx` 出现在列表项里会被 markdown 当标题渲染。
    _tcore = re.sub(r"^(?:[-*•>]\s+|#{1,6}\s*)+", "", title).strip()

    def _clean_bullet(raw: str) -> str:
        """脱掉前导符号/标题记号；只脱「符号+空白」或 `#`，别伤到 '-5 度' 这类。"""
        return re.sub(r"^(?:[-*•>]\s+|#{1,6}\s*)+", "", str(raw or "")).strip()

    _seen_b = set()
    _clean_bullets = []
    for _b in bullets:
        _cb = _clean_bullet(_b)
        if not _cb or _cb == _tcore:          # 空 / 就是标题本身 → 丢掉
            continue
        if _cb in _seen_b:                    # 脱壳后可能撞车 → 去重
            continue
        _seen_b.add(_cb)
        _clean_bullets.append(_cb)
    bullets = _clean_bullets
    body = strip_web_noise(e.get("text") or "")
    related = [str(r).strip() for r in (e.get("related") or []) if str(r).strip()]
    conf = float(e.get("conf") or 0.0)

    fm = ["---",
          "title: " + _yaml_str(title),
          "tags: [" + ", ".join(_yaml_str(t) for t in tags) + "]",
          "category: " + _yaml_str(cat),
          "source: " + _yaml_str(e.get("src") or ""),
          "created: " + _yaml_str(e.get("date") or ""),
          "updated: " + _yaml_str(e.get("date") or ""),
          "confidence: %.2f" % conf,
          "reads: %d" % int(e.get("n_read") or 1),
          "pasm: true",
          "---", ""]
    if not bullets and body:
        bullets = sentence_bullets(body, 6)     # 没要点时现场补一份速记索引
    summary = bullets[0] if bullets else (body[:120] if body else "（这条还没提炼出内容）")
    parts = fm + ["# " + title, "",
                  "> [!note] " + summary.replace("\n", " ")[:200], ""]
    if bullets:
        parts += ["## 要点", ""] + ["- " + b for b in bullets] + [""]
    if body:
        parts += ["## 全文", "", body, ""]
    else:
        parts += ["## 全文", "", "（这条没有存到全文，只有上面的要点速记。）", ""]
    if related:
        parts += ["## 相关", ""] + ["- [[" + r + "]]" for r in related] + [""]
    if e.get("src"):
        parts += ["## 来源", "", str(e["src"]), ""]
    parts += ["---", "",
              "*由 PASM Studio 资料库生成；可直接用 Obsidian 打开本文件夹作为库。*", ""]
    return "\n".join(parts)


def _write_if_changed(path: str, content: str) -> bool:
    """内容没变就不写（幂等）。返回是否真的写了。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            if f.read() == content:
                return False
    except Exception:                       # noqa: BLE001  读不到=当没写过
        pass
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
    os.replace(tmp, path)                   # 原子替换，避免中途崩掉留半截文件
    return True


def vault_index_md(know: Optional[List[dict]] = None) -> str:
    """目录页（MOC）：按分类列出全部 `[[笔记]]`。"""
    know = know if know is not None else _load_know()
    groups: Dict[str, List[str]] = {}
    for e in know:
        groups.setdefault(str(e.get("cat") or "通用"), []).append(_title_of(e))
    total_body = sum(len(e.get("text") or "") for e in know)
    out = ["---", "title: " + _yaml_str("资料库目录"), "tags: [\"MOC\", \"资料库\"]",
           "pasm: true", "---", "", "# 📚 资料库目录", "",
           "共 **%d** 条笔记 · 全文合计约 **%d** 字。"
           % (len(know), total_body),
           "",
           "> 直接用 Obsidian「打开文件夹作为库」即可浏览、双链跳转、全文搜索。", ""]
    for cat in sorted(groups):
        out += ["## " + cat, ""]
        for t in sorted(groups[cat]):
            out.append("- [[" + t + "]]")
        out.append("")
    out += ["---", "", "*本页由 PASM Studio 资料库自动生成。*", ""]
    return "\n".join(out)


def export_vault(force: bool = False) -> dict:
    """把全部知识导出成 `.md` 笔记 + `index.md`（幂等）。

    force=False 时内容没变就不重写（学习一次就重写全库会很浪费）。
    返回 {ok, dir, notes, wrote, skipped, err}。
    """
    res = {"ok": True, "dir": vault_dir(), "notes": 0, "wrote": 0,
           "skipped": 0, "err": ""}
    try:
        os.makedirs(res["dir"], exist_ok=True)
        know = _load_know()
        res["notes"] = len(know)
        for e in know:
            md = note_markdown(e)
            path = os.path.join(res["dir"], _safe_filename(_title_of(e)) + ".md")
            if _write_if_changed(path, md):
                res["wrote"] += 1
            else:
                res["skipped"] += 1
        # 目录页
        ip = os.path.join(res["dir"], _VAULT_INDEX)
        if _write_if_changed(ip, vault_index_md(know)):
            res["wrote"] += 1
        res["notes"] += 1                    # 含目录页
    except Exception as ex:                  # noqa: BLE001  失败要如实说，不假装导出成功
        res["ok"] = False
        res["err"] = "%s: %s" % (type(ex).__name__, ex)
    return res


def export_one(title: str) -> bool:
    """只导出一条（学习完立刻落笔记用）。返回是否写了。"""
    e = find_entry(title)
    if not e:
        return False
    try:
        os.makedirs(vault_dir(), exist_ok=True)
        path = os.path.join(vault_dir(), _safe_filename(_title_of(e)) + ".md")
        wrote = _write_if_changed(path, note_markdown(e))
        _write_if_changed(os.path.join(vault_dir(), _VAULT_INDEX),
                          vault_index_md())
        return wrote
    except Exception:                        # noqa: BLE001
        return False


def read_note(title: str) -> str:
    """读一条笔记的 Markdown（找不到返回空串）。"""
    p = os.path.join(vault_dir(), _safe_filename(title) + ".md")
    try:
        with open(p, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:                        # noqa: BLE001
        return ""


def vault_stats() -> dict:
    """资料库现状：目录 / 笔记数 / 全文字数 / 是否有目录页。"""
    d = vault_dir()
    n = 0
    try:
        n = len([f for f in os.listdir(d) if f.lower().endswith(".md")])
    except Exception:                        # noqa: BLE001
        n = 0
    know = _load_know()
    return {"dir": d, "notes": n, "entries": len(know),
            "chars": sum(len(e.get("text") or "") for e in know),
            "has_index": os.path.exists(os.path.join(d, _VAULT_INDEX)),
            "exists": os.path.isdir(d)}


def open_vault() -> dict:
    """打开资料库文件夹：**先确保笔记真写出来了，再打开**；打不开就如实说。

    返回 `{ok, dir, opened, notes, err}`，两个状态**分开报**：

    - `ok`     = 笔记导出成功（内容可用）
    - `opened` = 文件夹真的被唤起来了

    为什么必须分开（v0.30.8 实测踩到）：
      ① 目录可能压根没建出来（磁盘满 / 无权限 / 被杀软拦），这时去 startfile
         一个不存在的路径，系统会弹**「位置不可用」对话框**，看着像软件坏了；
      ② 打开失败的原因可能只是「没有默认打开方式」，笔记本身一点问题没有。
    旧版把两者混成一个返回值、还吞掉全部异常 → 调用方**永远显示"已打开"**，
    用户找不到窗口只会以为资料库丢了。

    **只导出、只打开文件夹** —— 不偷偷装 Obsidian、不改用户任何设置。
    """
    r = export_vault()
    d = vault_dir()
    res = {"ok": False, "dir": d, "opened": False,
           "notes": r.get("notes") or 0, "err": str(r.get("err") or "")}
    if not os.path.isdir(d):
        # 兜底：别把一个不存在的路径交给系统去开
        res["err"] = res["err"] or "资料库目录不存在（导出未成功）"
        return res
    res["ok"] = bool(r.get("ok"))
    fn = _opener
    if fn is None:
        # 跨平台：platform_ops.startfile 在 Windows=os.startfile / macOS=open / Linux=xdg-open
        # （旧版只在 Windows 上给 fn，非 Windows 一律"不支持自动打开"）
        try:
            import platform_ops
            fn = platform_ops.startfile
        except Exception:
            fn = None
    if fn is None:
        res["err"] = "当前系统不支持自动打开文件夹，请手动打开上面的路径"
        return res
    try:
        fn(d)                                # noqa: S606  用户主动点开才走这里
        res["opened"] = True
    except Exception as ex:                  # noqa: BLE001  打不开要如实说，不谎报
        res["err"] = str(ex)
    return res


def selftest() -> int:
    """自检：**全程在临时目录**，不碰用户真实资料库。"""
    import shutil
    import tempfile
    global DATA_DIR, BOOKS_DIR, KNOW_FILE, _KNOW_CACHE, _opener
    old = (DATA_DIR, BOOKS_DIR, KNOW_FILE, dict(_KNOW_CACHE))
    passed = failed = 0

    def check(label, ok, extra=""):
        nonlocal passed, failed
        if ok:
            passed += 1
            print("  [PASS] %s %s" % (label, extra))
        else:
            failed += 1
            print("  [FAIL] %s %s" % (label, extra))

    tmp = tempfile.mkdtemp(prefix="pasm_kb_")
    try:
        DATA_DIR = tmp
        BOOKS_DIR = os.path.join(tmp, "books")
        KNOW_FILE = os.path.join(tmp, "knowledge.json")
        _KNOW_CACHE = {"ts": 0.0, "data": None}
        os.makedirs(BOOKS_DIR, exist_ok=True)

        print("-- 网页噪声清洗（这是「资料不全」的真凶）--")
        noisy = ("首页\n登录\n注册\n加载中\n"
                 "Python 是一种广泛使用的高级编程语言，由 Guido van Rossum 创造。\n"
                 "相关搜索\n广告\nPython 是一种广泛使用的高级编程语言，由 Guido van Rossum 创造。\n"
                 "- - - -\n1\n更多\n"
                 "它的设计哲学强调代码的可读性和简洁的语法。")
        clean = strip_web_noise(noisy)
        check("清掉了导航词（首页/登录/注册）",
              "首页" not in clean and "登录" not in clean and "注册" not in clean)
        check("清掉了广告/相关搜索", "相关搜索" not in clean and "广告" not in clean)
        check("清掉了重复段（只留一份）",
              clean.count("Guido van Rossum") == 1, clean.count("Guido van Rossum"))
        check("**真内容留下**（这是关键，别过度清洗）",
              "高级编程语言" in clean and "可读性" in clean)
        check("反例：正常短文不会被清空",
              len(strip_web_noise("水在零度结冰，一百度沸腾。")) >= 10)
        check("空输入不炸", strip_web_noise("") == "")
        check("None 不炸", strip_web_noise(None) == "")

        print("-- 笔记渲染 --")
        record("网络安全入门", ["强密码要足够长", "开启两步验证"],
               text="强密码要足够长并且不复用。\n\n开启两步验证能挡住绝大多数撞库。",
               src="网页自学：https://example.com/a")
        record("密码学基础", ["哈希不可逆", "加盐防彩虹表"],
               text="哈希函数是单向的。加盐可以防彩虹表。")
        e = find_entry("网络安全入门")
        check("条目已入库", bool(e))
        md = note_markdown(e)
        check("有 YAML frontmatter", md.startswith("---\n") and "title:" in md)
        check("frontmatter 收尾", "\n---\n" in md)
        check("有 H1 标题", "# 网络安全入门" in md)
        check("有 Obsidian callout 概要", "> [!note]" in md)
        check("有「要点」段", "## 要点" in md and "- 强密码要足够长" in md)
        check("有「全文」段且是真内容",
              "## 全文" in md and "两步验证能挡住" in md)
        check("有「来源」段（可追溯）", "## 来源" in md and "example.com" in md)
        check("文件名后缀不会跑进标题",
              ".md" not in _title_of(e) and "网络安全入门" == _title_of(e))

        print("-- 双链（Obsidian 的 [[wikilink]]）--")
        _refresh_links(_load_know())
        rel = related_of("网络安全入门")
        check("相关条目识别（密码/安全 同域）", any("密码" in r for r in rel), rel)

        print("-- 导出资料库（幂等）--")
        r1 = export_vault()
        check("导出成功", r1["ok"] is True, r1.get("err"))
        check("真的写了文件", r1["wrote"] >= 3, r1["wrote"])
        check("目录页存在", os.path.exists(os.path.join(vault_dir(), "index.md")))
        r2 = export_vault()
        check("**幂等：第二次不再重写**（内容没变）", r2["wrote"] == 0, r2["wrote"])
        check("笔记文件真实可读",
              "## 全文" in read_note("网络安全入门"))
        idx = vault_index_md()
        check("目录页含双链", "[[网络安全入门]]" in idx and "[[密码学基础]]" in idx)
        check("目录页按分类分组", "## 安全" in idx)

        # ---- 要点清洗（v0.30.8）：真实数据里 18/23 条的首个「要点」就是标题本身 ----
        # 用**合成的脏数据**钉住，以后回归立刻显形。
        md_dirty = note_markdown({
            "title": "脏数据示例",
            "bullets": ["# 脏数据示例", "- # 脏数据示例", "真要点一",
                        "真要点一", "-5 度不是要点但也不能被改坏"],
            "text": "正文若干。",
            "cat": "测试", "src": "自检",
        })
        # ⚠️ 切片要用「下一节标题」切，不能用 "\n\n" —— `## 要点` 后面紧跟着
        # 一个空行，`split("\n\n")[0]` 会直接得到空串（本轮我自己写错过一次）。
        _body = md_dirty.split("## 要点", 1)[1].split("## ", 1)[0]
        check("要点清洗：纯标题（含 # 前缀）被丢掉",
              "# 脏数据示例" not in _body, _body)
        check("要点清洗：脱壳后重复的只留一条",
              _body.count("真要点一") == 1, _body)
        check("要点清洗：'-5 度…' 这类不被误伤（前导没有符号+空白）",
              "-5 度不是要点但也不能被改坏" in _body, _body)
        _callout = [ln for ln in md_dirty.split("\n") if ln.startswith("> [!note]")]
        check("callout 不再以 '# 标题' 开头（=没说）",
              _callout and not _callout[0].lstrip("> [!note] ").startswith("#"),
              _callout)
        check("callout 用的是清洗后的第一条真要点",
              _callout and "真要点一" in _callout[0], _callout)
        # 反例：要点全是标题 + 有**足够长的**全文 → 仍走"现场补速记索引"。
        # ⚠️ `sentence_bullets` 只收「脱掉标点后 ≥8 字」的句子（见其实现）——
        #    我第一版用例给的两句各只有 7 字，于是补不出要点，是**用例写错**不是代码错。
        md_only_title = note_markdown({
            "title": "只有标题", "bullets": ["# 只有标题"],
            "text": "第一句是真内容，讲的是基本概念。第二句也有用，说明怎么用。",
            "cat": "测试", "src": "自检"})
        check("要点被清空时：仍从全文现场补速记索引",
              "## 要点" in md_only_title and "第一句是真内容" in md_only_title,
              md_only_title.split("## 要点", 1)[1][:80])

        # 反例（更极端的短正文）：补不出要点也要有底线 —— **原文一个字都不许丢**。
        md_short = note_markdown({
            "title": "短正文", "bullets": ["# 短正文"], "text": "短也要留着。",
            "cat": "测试", "src": "自检"})
        check("短正文补不出要点时：不生成空的 ## 要点 节",
              "## 要点" not in md_short, md_short[:120])
        check("短正文补不出要点时：原文仍完整保留在笔记里",
              "短也要留着。" in md_short, md_short[:200])
        check("短正文的 callout 不为空",
              "> [!note] 短也要留着。" in md_short,
              [ln for ln in md_short.split("\n") if ln.startswith("> [!note]")])
        st = vault_stats()
        check("统计：条目/笔记/字数", st["entries"] == 2 and st["notes"] >= 3
              and st["chars"] > 20, st)
        check("反例：不存在的笔记读出来是空串（不报错）",
              read_note("根本没有这条") == "")

        print("-- 一条重学：全文并集（不因提炼要点而丢原文）--")
        record("网络安全入门", ["补充：定期更换泄露过的密码"],
               text="如果某个站泄露了，立刻改掉同密码的所有站点。")
        e2 = find_entry("网络安全入门")
        check("要点是并集（旧+新）", len(e2["bullets"]) >= 3, e2["bullets"])
        # ⚠️ 我原来这条断言写反了：`record()` 的语义是**保留更长的一份原文**
        # （重复学习不该把已存的更全原文换成更短的）。第二次用的是更短文本，
        # 所以库里应当**仍是原来那份更全的**，同时新要点已并集进来。
        check("重复学习：原文保留更长的一份（不被更短的覆盖）",
              "两步验证能挡住" in (e2.get("text") or "")
              and "同密码的所有站点" not in (e2.get("text") or ""),
              (e2.get("text") or "")[:40])
        # 新并进来的是**要点**那句（"补充：定期更换泄露过的密码"）；
        # "同密码的所有站点"只出现在原文里，不在要点里 —— 断言要认对字段。
        check("重复学习：新要点仍然并集进来",
              any("定期更换" in b for b in e2["bullets"]), e2["bullets"])

        # ---- open_vault：真打开 / 假打开必须分得开（v0.30.8）----
        # 全程走注入点打桩，**绝不真弹资源管理器**（这正是本轮事故的源头）。
        _seen = []
        _old_opener = _opener
        try:
            _opener = lambda p: _seen.append(p)          # noqa: E731
            r1 = open_vault()
            check("open_vault：目录在 → 真调打开器，opened=True",
                  r1["opened"] is True and _seen == [r1["dir"]], (r1, _seen))
            check("open_vault：dir 就是资料库目录", r1["dir"] == vault_dir(), r1["dir"])
            check("open_vault：返回 ok 为 True", r1["ok"] is True, r1)

            def _boom(_p):
                raise OSError("没有默认打开方式")

            _opener = _boom
            r2 = open_vault()
            check("open_vault：打开器抛错 → opened=False 且带原因（不许谎报已打开）",
                  r2["opened"] is False and "打开方式" in r2["err"], r2)
            check("open_vault：打开失败但笔记是好的 → ok 仍为 True",
                  r2["ok"] is True, r2["ok"])
        finally:
            _opener = _old_opener

        # 反例：目录根本建不出来（拿一个**文件**当父目录 → makedirs 必失败）
        _blocker = os.path.join(tmp, "blocker.txt")
        with open(_blocker, "w", encoding="utf-8") as fh:
            fh.write("x")
        _keep = DATA_DIR
        try:
            DATA_DIR = os.path.join(_blocker, "sub")
            r3 = open_vault()
            check("open_vault：目录建不出来 → ok=False / opened=False（不把死路径丢给系统）",
                  r3["ok"] is False and r3["opened"] is False and bool(r3["err"]), r3)
        finally:
            DATA_DIR = _keep
    finally:
        DATA_DIR, BOOKS_DIR, KNOW_FILE, _KNOW_CACHE = old[0], old[1], old[2], old[3]
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n%d 项通过 · %d 失败" % (passed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        raise SystemExit(selftest())
    if "--export" in sys.argv:
        r = export_vault()
        print("资料库：%s（%d 条，写了 %d 个文件）" % (r["dir"], r["notes"], r["wrote"]))
        raise SystemExit(0 if r["ok"] else 1)
    print(__doc__)
