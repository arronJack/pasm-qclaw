"""worklog.py —— 工作任务台账（v0.27.4）

真机需求（小志）：**让"聊天"和"工作"关联起来**——
  · 在聊天里让它做某项工作，过程/结果要能"落在那项工作里"，而不是散在聊天流；
  · 聊天内容不变（工作日志不污染主聊天）；
  · 工作中也能聊天：每个工作有自己的**讨论区**，专门聊这个工作的修改与进度；
  · 桌面小人在工作状态时，显示当前在做的工作内容。

设计：把"它正在干的活"做成一等实体（WorkTask），而不是只列磁盘产物。
  · 每次聊天触发重活（开发/PPT/Word/表格/视频/图像/漫剧/脚本/自学）→ 开一条任务；
  · 任务带：标题 / 类型 / 状态(running|done|failed) / 进度 / 产物路径 / 所属会话 / 讨论记录；
  · 工作台能看见任务，点开进"工作详情"，在里面就地讨论；
  · 桌面小人可读到"当前工作"标签。

存储：%APPDATA%/PASMStudio/work_tasks.json（与应用其它数据同目录）。
"""
from __future__ import annotations

import json
import os
import re
import time

_LOCK = None
_MAX_TASKS = 200
_MAX_CHAT = 120


def _data_dir() -> str:
    d = os.environ.get("PASM_STUDIO_DIR")
    if d:
        return d
    if os.name == "nt":
        return os.path.join(os.environ.get("APPDATA")
                            or os.path.expanduser("~"), "PASMStudio")
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        ".pasmstudio_dev")


def _path() -> str:
    return os.path.join(_data_dir(), "work_tasks.json")


def _load() -> dict:
    try:
        with open(_path(), encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict) and isinstance(d.get("tasks"), list):
            return d
    except Exception:
        pass
    return {"tasks": []}


def _save(d: dict):
    try:
        os.makedirs(_data_dir(), exist_ok=True)
        tmp = _path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        os.replace(tmp, _path())
    except Exception:
        pass


_KIND_LAB = {
    "project": "🖥 开发", "script": "🧾 脚本", "genppt": "📽 PPT", "gendoc": "📄 Word",
    "genxls": "📊 表格", "ppt": "📽 PPT", "doc": "📄 Word", "xls": "📊 表格",
    "video": "🎬 视频", "image": "🎨 图像", "manga": "📖 漫剧",
    "weblearn": "🌐 自学", "skill": "🧩 技能", "copy": "✍️ 文案",
    "sysops_clean": "🧹 清理", "sysops_del": "🗑 删除", "sysops_del_paths": "🗑 删除",
    "websearch": "🔍 搜索", "secchk": "🛡 体检", "general": "🛠 工作", "ad": "📣 广告", "workflow": "🔗 工作流",
    # v0.28.x 新增能力
    "remind_add": "⏰ 定时提醒", "remind_del": "⏰ 取消提醒",
    "mail_send": "📧 发邮件", "cal_add": "📅 加日程", "cal_del": "📅 删日程",
    "evolve_save": "🧬 沉淀技能", "team_auto": "🧩 自动组队",
}


def kind_label(kind: str) -> str:
    return _KIND_LAB.get(kind or "", "🛠 " + (kind or "工作"))


#: v0.30.5：进程内自增序号 —— 同一毫秒内连建任务也不撞 id
_ID_SEQ = [0]


def _new_id() -> str:
    """任务 id：秒 + 毫秒 + 进程内自增序号。

    v0.30.5：原为"秒 + 毫秒%1000"，同一毫秒内连续调用会得到相同 id
    （同款写法在 media_job 上已实测 6 个任务撞成 1 个 id）→ 按 id 覆盖会静默丢任务。
    加自增序号后同进程内绝不重复。
    """
    _ID_SEQ[0] = (_ID_SEQ[0] + 1) % 100000
    return "t%d%03d%05d" % (int(time.time()), int(time.time() * 1000) % 1000,
                            _ID_SEQ[0])


def create(title: str, kind: str, session: str = "", dir: str = "",
           next_step: str = "", parent_id: str = "", level: int = 0) -> dict:
    """开一条工作任务。返回任务 dict。"""
    d = _load()
    t = {"id": _new_id(),
         "title": (title or "").strip()[:60] or "(未命名工作)",
         "kind": kind or "general",
         "status": "running",
         "progress": "已开工…",
         "created": time.time(),
         "updated": time.time(),
         "artifacts": [],
         "session": session or "",
         "chat": [],
         "dir": (dir or "").strip()[:60],
         "next_step": (next_step or "").strip()[:200],
         "parent_id": parent_id or "",
         "level": int(level)}
    d["tasks"].append(t)
    # 只保留最近 N 条，防止无限膨胀
    if len(d["tasks"]) > _MAX_TASKS:
        d["tasks"] = d["tasks"][-_MAX_TASKS:]
    _save(d)
    return t


def get(tid: str):
    for t in _load()["tasks"]:
        if t.get("id") == tid:
            return t
    return None


def update(tid: str, **kw):
    d = _load()
    for t in d["tasks"]:
        if t.get("id") == tid:
            t.update(kw)
            t["updated"] = time.time()
            break
    _save(d)


def set_status(tid: str, status: str, progress: str = ""):
    d = _load()
    for t in d["tasks"]:
        if t.get("id") == tid:
            t["status"] = status
            if progress:
                t["progress"] = progress
            t["updated"] = time.time()
            break
    _save(d)


def add_artifact(tid: str, path: str):
    path = (path or "").strip()
    if not path:
        return
    d = _load()
    for t in d["tasks"]:
        if t.get("id") == tid:
            arts = t.setdefault("artifacts", [])
            if path not in arts:
                arts.append(path)
            t["updated"] = time.time()
            break
    _save(d)


_PATH_RE = re.compile(
    # ⚠️ `(?<![A-Za-z])` 不能去掉：`file:///C:/…` 里的 `e:` 会被当成 E 盘路径，
    #    抠出 `e:\\\C:\Users\…` 这种谁也打不开的东西（真机台账里就有这一条）。
    r"(?<![A-Za-z])[A-Za-z]:[\\/][^\s\"'<>|，。；：、）\]]+"
    r"|(?:/[^\s\"'<>|，。；：、）\]]+){2,}")

#: markdown 链接 / 图片目标：`![图01](file:///C:/…/%E5%9B%BE01.png)`
_MD_URI_RE = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)")
#: 反引号里的整段（**PASM 自己就是这么跟用户报路径的**：`` `C:\…\方案.md` ``）
_TICK_RE = re.compile(r"`([^`\n]{4,})`")
_PATH_HEAD_RE = re.compile(r"^(?:[A-Za-z]:[\\/]|/|file:)", re.I)
#: 结尾句读（路径被写在句子里时会被粘上）：只在"去掉后确实存在"时才去
_TAIL_JUNK = "，。、；：）】」』>,.;:)"


def _path_exists(p: str) -> bool:
    try:
        return bool(p) and os.path.exists(p)
    except Exception:                                        # noqa: BLE001
        return False


def _norm_path(p: str) -> str:
    """把"从回复里抠到的一段"整成可用的本地路径。

    v0.30.13：以前**没有这一步** —— markdown 图片链接会被抠成
    `///C:/…/%E5%9B%BE01.png`（`file:` 没剥、百分号编码没解），
    台账里于是躺着一条谁也打不开的产物。
    """
    p = (p or "").strip().strip("`\"'<>")
    if p.lower().startswith("file:"):
        p = p[5:]
        while p.startswith("//"):
            p = p[1:]
    if "%" in p:
        try:
            from urllib.parse import unquote
            p = unquote(p)
        except Exception:                                    # noqa: BLE001
            pass
    p = p.strip().strip("`\"'<>")
    # ① 开头多余斜杠：`file:///C:/x` 抠出来是 `///C:/x` → 削到盘符为止
    _q = p.lstrip("\\/")
    if _q != p and re.match(r"^[A-Za-z]:", _q):
        p = _q
    # ② `X:\\…`：`file:` 的前半段被抠掉后的产物（`e:\\\C:\…`）→ 去掉 `X:` 与斜杠
    if re.match(r"^[A-Za-z]:[\\/]{2,}", p):
        p = p[2:].lstrip("\\/")
    if re.match(r"^[A-Za-z]:[/\\]", p):                      # C:/x → C:\x
        p = p.replace("/", "\\")
    if not _path_exists(p):
        for cut in (1, 2):                                   # 粘着的句读
            q = p[:-cut].rstrip() if cut < len(p) else ""
            if q and _path_exists(q):
                p = q
                break
    return p


def extract_paths(text: str, limit: int = 6) -> list:
    r"""从一段回复里抠出可能的产物路径（用于自动记进任务的产物列表）。

    v0.30.13 重做。以前只有一条正则硬抠，遇到两种**我们自己在用的**写法就会抠错：
      · markdown 图片 `![图01](file:///C:/…/%E5%9B%BE01.png)` → 抠出
        `///C:/…/%E5%9B%BE01.png`（`file:` 没剥、百分号编码没解）；
      · 路径里带「【】」—— **PASM 自己的产物目录名就长这样**
        （`image\为【帮我设计…】设计一张【现_145925\`）—— 正则把 `】` 当句读，
        从那里**截断**，台账里于是记下一条不存在的路径。
      实测（2026-09-17 真机台账）：一条出图任务的 2 个"产物"**两个都是残的**。

    三轮取（① markdown 链接/图片目标 ② 反引号里的整段 ③ 兜底正则），
    按**它们在原文里出现的先后**排；再让磁盘上真存在的排前面
    （抠错的/只是"建议路径"的排后面，不会挤掉真产物）。
    """
    cand = []

    def _push(p, pos):
        p = _norm_path(p)
        if len(p) > 6 and p not in [c[1] for c in cand]:
            cand.append((pos, p))

    txt = text or ""
    for m in _MD_URI_RE.finditer(txt):
        _push(m.group(1), m.start())
    for m in _TICK_RE.finditer(txt):
        c = m.group(1).strip()
        if _PATH_HEAD_RE.match(c):
            _push(c, m.start())
    for m in _PATH_RE.finditer(txt):
        _push(m.group(0), m.start())
    ordered = [p for _pos, p in sorted(cand, key=lambda x: x[0])]
    real = [p for p in ordered if _path_exists(p)]
    fake = [p for p in ordered if not _path_exists(p)]
    return (real + fake)[:limit]


def finish(tid: str, reply: str = "", ok: bool = True):
    """任务收尾：状态 + 从回复里提取产物路径。"""
    st = "done" if ok else "failed"
    for p in extract_paths(reply)[:6]:
        add_artifact(tid, p)
    set_status(tid, st, "✅ 已完成" if ok else "❌ 失败/中断")


def add_chat(tid: str, role: str, content: str):
    d = _load()
    for t in d["tasks"]:
        if t.get("id") == tid:
            c = t.setdefault("chat", [])
            c.append({"role": role, "content": content, "t": time.time()})
            if len(c) > _MAX_CHAT:
                t["chat"] = c[-_MAX_CHAT:]
            t["updated"] = time.time()
            break
    _save(d)


def get_chat(tid: str) -> list:
    t = get(tid)
    return (t or {}).get("chat", [])


def recent(n: int = 30) -> list:
    """最近的任务（新的在前）。"""
    ts = _load()["tasks"]
    ts = sorted(ts, key=lambda x: x.get("updated", x.get("created", 0)), reverse=True)
    return ts[:n]


def running() -> list:
    return [t for t in recent(50) if t.get("status") == "running"]


def mark_running_as_interrupted() -> int:
    """v0.27.6：把卡在 running 的任务标为 interrupted，返回改动条数。

    真机问题（小志 2026-09-10）：干活干到一半蓝屏/关程序 → 任务永远卡在
    "进行中"，看不出它其实早就断了。程序重启后必然跑不到收尾逻辑，
    所以启动时统一把仍是 running 的判为"被硬中断"，如实改标，
    让用户知道这条需要重跑/续跑。
    """
    d = _load()
    n = 0
    now = time.strftime("%Y-%m-%d %H:%M")
    for t in d.get("tasks", []):
        if str(t.get("status") or "") == "running":
            t["status"] = "interrupted"
            t["interrupted_at"] = now
            t["progress"] = "⚠️ 已中断（程序退出/蓝屏，未跑完）——可重新开工"
            t["updated"] = time.time()
            n += 1
    if n:
        _save(d)
    return n


def unfinished() -> list:
    """未完成或被中断的任务（running / interrupted），新的在前。"""
    return [t for t in recent(50)
            if t.get("status") in ("running", "interrupted")]


def last_unfinished() -> dict:
    """最近一条未完成的活（供界面提示"上次没干完，要不要接着干"）。"""
    us = unfinished()
    return us[0] if us else {}


def current_label() -> str:
    """给桌面小人用：当前正在做的工作名（没有则空串）。"""
    rs = running()
    if not rs:
        return ""
    t = rs[0]
    return "%s %s" % (kind_label(t.get("kind")), t.get("title", "")[:18])


def clear_finished(keep_running_only: bool = False):
    d = _load()
    if keep_running_only:
        d["tasks"] = [t for t in d["tasks"] if t.get("status") == "running"]
    else:
        d["tasks"] = []
    _save(d)

# ---------- v0.30.4：目录 / 层级 / 下一步 / 进度 ----------

def set_next(tid: str, next_step: str):
    """设定某工作的「下一步」（面板复盘与续跑用）。"""
    update(tid, next_step=(next_step or "").strip()[:200])


def by_dir(d: str, n: int = 50) -> list:
    """按工作目录筛选（支持「不同目录查看」复盘）。"""
    d = (d or "").strip()
    if not d:
        return recent(n)
    return [t for t in recent(n) if (t.get("dir") or "") == d]


def children_of(parent_id: str) -> list:
    """某父工作的子任务（层级结构）。"""
    return [t for t in recent(200) if (t.get("parent_id") or "") == parent_id]


def progress_of(root_id: str) -> dict:
    """汇总某工作流（父）整体进度：子任务数 / 完成数 / 百分比。"""
    kids = children_of(root_id)
    if not kids:
        t = get(root_id)
        done = 1 if (t or {}).get("status") == "done" else 0
        return {"total": 1, "done": done, "pct": 100 if done else 0}
    total = len(kids)
    done = sum(1 for k in kids if k.get("status") == "done")
    return {"total": total, "done": done, "pct": int(round(100 * done / total))}


def step_complete(tid: str, progress: str = "", ok: bool = True):
    """完成一个子步骤，并回写父工作流整体进度。"""
    set_status(tid, "done" if ok else "failed",
               progress or ("✅ 步骤完成" if ok else "❌ 步骤失败"))
    t = get(tid)
    pid = (t or {}).get("parent_id")
    if pid:
        s = progress_of(pid)
        set_status(pid, "running", "🔧 进行中 %d%%（%d/%d 步）" % (s["pct"], s["done"], s["total"]))
        if s["pct"] >= 100:
            set_status(pid, "done", "🎉 工作流全部完成（%d 步）" % s["total"])


def _selftest() -> int:
    """自检：任务 CRUD / 目录复盘 / 层级进度回写 / id 唯一（含反例）。

    自带临时数据目录，跑完还原 —— 不碰用户真实工作记录。
    """
    import shutil
    import tempfile

    fails = []
    CNT = [0]

    def check(name, cond, extra=""):
        print("  %s %s%s" % ("[OK]  " if cond else "[FAIL]", name,
                             ("  " + str(extra)) if (extra and not cond) else ""))
        CNT[0] += 1
        if not cond:
            fails.append(name)

    print("=== worklog 自检 ===")
    saved = os.environ.get("PASM_STUDIO_DIR")
    tmp = tempfile.mkdtemp(prefix="worklog_selftest_")
    os.environ["PASM_STUDIO_DIR"] = tmp
    try:
        # ① id 唯一（同批连建，反例：撞车互相覆盖）
        burst = [create("批量%d" % i, "general")["id"] for i in range(12)]
        check("★同批创建的任务 id 两两不同（反例：id 撞车覆盖）",
              len(set(burst)) == len(burst),
              "%d 唯一 / %d" % (len(set(burst)), len(burst)))

        # ② 建任务：新字段全部落库
        t = create("开学季招生海报", "ad", dir="营销/海报",
                   next_step="出主视觉", level=0)
        check("create 落 dir", t["dir"] == "营销/海报", t["dir"])
        check("create 落 next_step", t["next_step"] == "出主视觉", t["next_step"])
        check("create 落 level", t["level"] == 0, t["level"])
        check("新任务默认 running", t["status"] == "running", t["status"])

        # ③ get / update / set_next
        check("get 能取回", (get(t["id"]) or {}).get("id") == t["id"])
        check("未知 id 返回 None（反例）", get("__不存在__") is None)
        set_next(t["id"], "写文案")
        check("set_next 生效", (get(t["id"]) or {}).get("next_step") == "写文案")
        set_status(t["id"], "running", "🔧 一半")
        check("set_status 生效", (get(t["id"]) or {}).get("progress") == "🔧 一半")

        # ④ 产物与路径抽取
        add_artifact(t["id"], "/tmp/out/poster.png")
        check("add_artifact 落库", "/tmp/out/poster.png" in (get(t["id"]) or {}).get("artifacts", []))
        got = extract_paths("结果在 E:\\AI\\out\\a.md 和 /tmp/b.png 里")
        check("extract_paths 抽到路径", len(got) >= 1, got)
        # v0.30.13：两种**我们自己就在用**的写法，改前会把路径抠残
        #（真机台账里一条出图任务的 2 个"产物"两个都是残的）
        _d = os.path.join(os.environ.get("PASM_STUDIO_DIR") or ".", "wl_probe")
        os.makedirs(_d, exist_ok=True)
        _md = os.path.join(_d, "为【xx】设计一张【现_145925】广告方案_奶茶.md")
        _png = os.path.join(_d, "图01.png")
        for _p, _b in ((_md, "x"), (_png, "\x89PNG")):
            with open(_p, "wb") as _f:
                _f.write(_b.encode("latin-1"))
        _g2 = extract_paths("📣 广告方案已落盘：`%s`\n\n✅ 图出好了：\n`%s`\n![图01](%s)"
                            % (_md, _d, "file:///" + _png.replace("\\", "/")))
        check("★含【】的路径不再被 `】` 截断（真机残路径的根因）", _md in _g2, _g2)
        check("★真产物排在前面（方案第一）", bool(_g2) and _g2[0] == _md, _g2)
        check("markdown 图片链接能还原（剥 file: / 解 %编码 / 斜杠归一）",
              _png in _g2, _g2)
        _g3 = extract_paths("建议存到 C:\\根本没有这个\\x.md，实际产物在 `%s`" % _md)
        check("不存在的「建议路径」排在真产物之后（反例）",
              bool(_g3) and _g3[0] == _md, _g3)
        _g4 = extract_paths("![图01](%s)" % ("file:///" + _png.replace("\\", "/")))
        check("★「file:」不被误当成 e: 盘（真机残路径的另一半根因）",
              _g4 == [_png], _g4)

        # ⑤ 目录复盘
        create("视频脚本", "video", dir="营销/视频")
        check("by_dir 只返回该目录", len(by_dir("营销/海报")) == 1,
              [x["dir"] for x in by_dir("营销/海报")])
        check("by_dir 未知目录返回空（反例）", by_dir("__没有这个目录__") == [])
        check("by_dir 空参数退回全部最近", len(by_dir("")) >= 2, len(by_dir("")))

        # ⑥ 层级 + 进度：1 个父 + 3 个子
        root = create("整季营销方案", "ad", dir="营销/海报")
        kids = [create("步骤%d" % i, "ad", dir="营销/海报", parent_id=root["id"],
                       level=1) for i in range(3)]
        check("children_of 找到 3 个子任务", len(children_of(root["id"])) == 3,
              len(children_of(root["id"])))
        check("无子任务时返回空（反例）", children_of(t["id"]) == [])
        p0 = progress_of(root["id"])
        check("初始进度 0%（0/3）",
              p0["total"] == 3 and p0["done"] == 0 and p0["pct"] == 0, p0)

        step_complete(kids[0]["id"])
        check("子步骤完成后 done=1", progress_of(root["id"])["done"] == 1,
              progress_of(root["id"]))
        check("父任务进度回写为 33%", progress_of(root["id"])["pct"] == 33,
              progress_of(root["id"]))
        check("父任务被置为 running",
              (get(root["id"]) or {}).get("status") == "running",
              (get(root["id"]) or {}).get("status"))

        step_complete(kids[1]["id"])
        step_complete(kids[2]["id"])
        p3 = progress_of(root["id"])
        check("全部完成后 100%", p3["pct"] == 100 and p3["done"] == 3, p3)
        check("父任务被置为 done",
              (get(root["id"]) or {}).get("status") == "done",
              (get(root["id"]) or {}).get("status"))

        step_complete(kids[0]["id"], ok=False)
        check("失败子步骤不算完成（反例）",
              progress_of(root["id"])["done"] == 2, progress_of(root["id"]))

        # ⑦ 叶子任务进度：没有子任务时按自身状态算（不是 0/0 除法错）
        leaf = create("单件工作", "general")
        check("叶子未完成 → 0%", progress_of(leaf["id"])["pct"] == 0,
              progress_of(leaf["id"]))
        finish(leaf["id"], reply="做完了", ok=True)
        check("叶子完成后 → 100%", progress_of(leaf["id"])["pct"] == 100,
              progress_of(leaf["id"]))
        check("未知 id 进度不抛错", progress_of("__不存在__")["pct"] == 0,
              progress_of("__不存在__"))

        # ⑧ 中断恢复 / 清理
        n_int = mark_running_as_interrupted()
        check("mark_running_as_interrupted 返回条数", isinstance(n_int, int), n_int)
        check("中断后没有 running（反例：假 running 挂着）",
              all(x.get("status") != "running" for x in recent(50)),
              [(x["title"], x["status"]) for x in recent(50) if x["status"] == "running"])
        clear_finished()
        check("clear_finished 后 done 被清掉",
              all(x.get("status") != "done" for x in recent(50)))
    finally:
        if saved is None:
            os.environ.pop("PASM_STUDIO_DIR", None)
        else:
            os.environ["PASM_STUDIO_DIR"] = saved
        shutil.rmtree(tmp, ignore_errors=True)

    if fails:
        print("-" * 46)
        print("自检失败 %d 项：%s" % (len(fails), "；".join(fails)))
        return 1
    print("-" * 46)
    print("自检通过（%d 项断言）" % CNT[0])
    return 0


if __name__ == "__main__":
    import sys as _sys
    _sys.exit(_selftest())
