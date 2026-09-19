"""scheduler —— PASM 定时 / 周期任务（v0.28.x）
================================================

对齐 WorkBuddy / OpenClaw / Hermes 的「定时任务」能力：让 PASM 不只是
被动回答问题，还能**到点自己动起来**（提醒、跑技能、生成日报、推送手机）。

设计原则（贴着小志的要求来）：
- **纯标准库**：只用 `threading` + `json` + `datetime`，不加任何依赖，
  安装包体积不变。
- **纯后台、不吃资源**：一个 30 秒醒一次的守护线程（`Event.wait` 休眠，
  不是忙等）。不跑任务时 CPU 占用为 0，可随时 `stop()`。
- **只暴露状态查询**：所有触发都写在本地 `schedules.json`，用户随时可查
  「我定了哪些任务、下次什么时候跑」，不弹窗、不打扰。

计划（when）四种形态，够用且好懂：
    {"kind": "once",     "at":   "2026-09-15T09:00"}        一次性
    {"kind": "daily",    "time": "09:00"}                   每天
    {"kind": "weekly",   "dow":  [0], "time": "09:00"}      每周（0=周一）
    {"kind": "interval", "minutes": 30}                     每 N 分钟

任务（job）结构：
    {"id","title","action","payload","when","enabled",
     "created","last_run","next_run","runs","last_result"}
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime, timedelta
from typing import Callable, List, Optional, Tuple

try:
    from pasm_companion import DATA_DIR          # 复用同一数据目录
except Exception:                                # 独立导入兜底（测试用）
    DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".pasmstudio_dev")

JOBS_PATH = os.path.join(DATA_DIR, "schedules.json")

# 轮询间隔（秒）。30 秒足够准（提醒场景人感知不到差别），又足够省电。
POLL_SECONDS = 30
# 程序关闭期间错过的任务：只补跑「最近 CATCHUP 分钟内」的，更早的直接跳过并顺延，
# 避免一开机被 50 条历史提醒刷屏。
CATCHUP_MINUTES = 10

_LOCK = threading.RLock()
_thread: Optional[threading.Thread] = None
_stop = threading.Event()
_on_fire: Optional[Callable[[dict], None]] = None

WEEK_CN = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
WEEK_LABEL = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
_CN_NUM = {"零": 0, "一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "十一": 11, "十二": 12}


# ---------------- 持久化 ----------------
def _load() -> List[dict]:
    try:
        with open(JOBS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save(jobs: List[dict]) -> bool:
    try:
        os.makedirs(os.path.dirname(JOBS_PATH), exist_ok=True)
        tmp = JOBS_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(jobs, f, ensure_ascii=False, indent=2)
        os.replace(tmp, JOBS_PATH)          # 原子替换：写一半断电也不丢旧数据
        return True
    except Exception:
        return False


# ---------------- 中文数字 / 时间解析 ----------------
def _cn_int(s: str) -> Optional[int]:
    """把「八」「十二」「20」解析成整数；失败返回 None。"""
    s = (s or "").strip()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    if s in _CN_NUM:
        return _CN_NUM[s]
    # 「二十一」这类两段式
    m = re.fullmatch(r"(一|二|两|三|四|五|六|七|八|九)十([一二三四五六七八九])?", s)
    if m:
        base = _CN_NUM[m.group(1)] * 10
        return base + (_CN_NUM.get(m.group(2), 0) if m.group(2) else 0)
    return None


def _hour24(text: str, h: int, minute: int) -> Tuple[int, int]:
    """按「上午/下午/晚上/凌晨/中午」校正小时。"""
    if any(w in text for w in ("下午", "晚上", "傍晚", "夜里", "深夜")) and 1 <= h <= 11:
        h += 12
    elif any(w in text for w in ("凌晨",)) and h == 12:
        h = 0
    elif "中午" in text and h < 11:
        h = 12
    return h % 24, minute % 60


_TIME_RE = re.compile(
    r"(?:(早上|上午|下午|晚上|傍晚|凌晨|中午|夜里|深夜)\s*)?"
    r"([0-9]{1,2}|[一二两三四五六七八九十]{1,3})\s*[:：点时]\s*"
    r"(半|[0-9]{1,2}|[一二三四五六七八九十]{1,3})?\s*分?")
_DOW_RE = re.compile(r"(?:每)?周([一二三四五六日天])")


def _minute_of(tok) -> int:
    if not tok:
        return 0
    if str(tok).strip() == "半":
        return 30
    v = _cn_int(str(tok))
    return v if v is not None else 0


def _parse_hhmm(text: str, default_h: int = 9,
                default_m: int = 0) -> Tuple[int, int, str]:
    """从文本里解析「HH:MM」。返回 (h, m, 命中的原文串)。

    ⚠️ 硬性要求：小时后面必须跟 `点 / 时 / : / ：` 之一。
    否则「每天提醒我一下」里的「一」会被当成 1 点 —— 这是初版真踩到的 bug。
    """
    m = _TIME_RE.search(text or "")
    if not m:
        return default_h, default_m, ""
    hh = _cn_int(m.group(2))
    if hh is None:
        return default_h, default_m, ""
    mm = _minute_of(m.group(3))
    h, mi = _hour24(m.group(0), hh, mm)
    return h, mi, m.group(0)


def parse_when(text: str) -> Tuple[Optional[dict], str]:
    """自然语言 → 计划 + 去掉时间短语后的剩余文字。

    支持：每天9点 / 每天早上八点半 / 每小时 / 每30分钟 / 每两个小时 /
          每周一9点 / 明天下午3点 / 后天9点 / 9月15日9点 / 今晚8点
    返回 (when 或 None, rest)。无法识别时间时 when=None。
    """
    raw = (text or "").strip()
    if not raw:
        return None, raw
    rest = raw
    now = datetime.now()

    # ---- 1) 间隔型：每小时 / 每30分钟 / 每两个小时 ----
    m = re.search(r"每\s*([0-9]{1,3}|[一二两三四五六七八九十]{1,3})?\s*(个)?\s*(小时|分钟|分)", raw)
    if m and ("每" in raw):
        unit = m.group(3)
        n_raw = m.group(1)
        n = _cn_int(n_raw) if n_raw else None
        if unit == "小时":
            minutes = (n or 1) * 60
        else:
            minutes = (n or 1)
        if 1 <= minutes <= 60 * 24 * 7:
            rest = raw.replace(m.group(0), " ")
            return {"kind": "interval", "minutes": int(minutes)}, _clean(rest)
    if re.search(r"每小时", raw):
        return {"kind": "interval", "minutes": 60}, _clean(raw.replace("每小时", " "))

    # ---- 2) 每周 X（时间只在「周X」之后找，否则「周一」的「一」会被当成 1 点）----
    md = _DOW_RE.search(raw)
    if md and "每" in raw:
        dow = WEEK_CN.get(md.group(1), 0)
        h, mi, hit = _parse_hhmm(raw[md.end():])
        rest = raw.replace(md.group(0), " ")
        if hit:
            rest = rest.replace(hit, " ")
        return {"kind": "weekly", "dow": [dow], "time": "%02d:%02d" % (h, mi)}, _clean(rest)

    # ---- 3) 每天 ----
    if "每天" in raw or "每日" in raw:
        h, mi, hit = _parse_hhmm(raw)
        rest = re.sub(r"(每天|每日)", " ", raw)
        if hit:
            rest = rest.replace(hit, " ")
        return {"kind": "daily", "time": "%02d:%02d" % (h, mi)}, _clean(rest)

    # ---- 4) 一次性：月初 / 明天 / 后天 / 今晚 / 今天 ----
    def _once(dt: datetime) -> dict:
        return {"kind": "once", "at": dt.strftime("%Y-%m-%dT%H:%M")}

    md = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]", raw)
    if md:
        mo, da = int(md.group(1)), int(md.group(2))
        h, mi, hit = _parse_hhmm(raw[md.end():])
        year = now.year if (mo, da) >= (now.month, now.day) else now.year + 1
        try:
            dt = datetime(year, mo, da, h, mi)
        except ValueError:
            return None, raw
        rest = raw.replace(md.group(0), " ")
        if hit:
            rest = rest.replace(hit, " ")
        return _once(dt), _clean(rest)

    day_off = None
    if "后天" in raw:
        day_off = 2
    elif "明天" in raw or "明早" in raw or "明晚" in raw:
        day_off = 1
    elif "今晚" in raw or "今天晚上" in raw:
        day_off = 0
    if day_off is not None:
        h, mi, hit = _parse_hhmm(raw, default_h=(20 if "晚" in raw else 9))
        base = now + timedelta(days=day_off)
        dt = base.replace(hour=h, minute=mi, second=0, microsecond=0)
        if day_off == 0 and dt <= now:
            dt += timedelta(days=1)          # 「今晚8点」已过 → 顺延到明晚
        rest = re.sub(r"(后天|明天|明早|明晚|今晚|今天晚上)", " ", raw)
        if hit:
            rest = rest.replace(hit, " ")
        return _once(dt), _clean(rest)

    return None, raw


def _clean(s: str) -> str:
    """清掉时间短语剥离后留下的悬空标点/助词。"""
    s = re.sub(r"^[\s,，。；;、:：的]+", "", s or "")
    s = re.sub(r"[\s,，。；;、:：]+$", "", s)
    s = re.sub(r"\s{2,}", " ", s).strip()
    return s


# ---------------- 下一次触发时间 ----------------
def next_fire(job: dict, after: Optional[datetime] = None) -> Optional[datetime]:
    """给定任务与起点时间，算下一次该跑的时刻；无（已停用/计划无效）返回 None。"""
    if not job.get("enabled", True):
        return None
    when = job.get("when") or {}
    now = after or datetime.now()
    kind = when.get("kind")
    try:
        if kind == "interval":
            mins = max(1, int(when.get("minutes") or 60))
            last = job.get("last_run") or ""
            if last:
                try:
                    base = datetime.fromisoformat(last)
                except Exception:
                    base = now
                nxt = base + timedelta(minutes=mins)
                while nxt <= now:
                    nxt += timedelta(minutes=mins)
                return nxt
            return now + timedelta(minutes=mins)
        if kind == "daily":
            hh, mm = _hhmm(when.get("time"))
            cand = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if cand <= now:
                cand += timedelta(days=1)
            return cand
        if kind == "weekly":
            dow = int((when.get("dow") or [0])[0]) % 7
            hh, mm = _hhmm(when.get("time"))
            cand = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            delta = (dow - cand.weekday()) % 7
            cand += timedelta(days=delta)
            if cand <= now:
                cand += timedelta(days=7)
            return cand
        if kind == "once":
            at = str(when.get("at") or "")
            dt = datetime.fromisoformat(at)
            return dt if dt > now else None      # 一次性且已过期 → 不再跑
    except Exception:
        return None
    return None


def _hhmm(s) -> Tuple[int, int]:
    m = re.match(r"^\s*(\d{1,2})\s*[:：]\s*(\d{1,2})", str(s or "9:00"))
    if not m:
        return 9, 0
    return int(m.group(1)) % 24, int(m.group(2)) % 60


# ---------------- 增删改查 ----------------
def list_jobs() -> List[dict]:
    with _LOCK:
        jobs = _load()
    # 顺手补上过期的 next_run，界面上永远显示「下次什么时候跑」
    for j in jobs:
        if not j.get("next_run"):
            nf = next_fire(j)
            j["next_run"] = nf.strftime("%Y-%m-%d %H:%M") if nf else ""
    return jobs


#: v0.30.5：进程内自增序号 —— 同一毫秒内连加多条任务也不撞 id
_JOB_SEQ = [0]


def _new_job_id() -> str:
    """任务 id：秒级时间戳 + 毫秒 + 自增序号。

    v0.30.5：原实现缺序号，同一毫秒内连续新增会得到相同 id（同款写法在
    media_job 上已实测撞车）→ 先建的那条被静默覆盖。
    """
    _JOB_SEQ[0] = (_JOB_SEQ[0] + 1) % 100000
    return "job%s%03d%05d" % (time.strftime("%y%m%d%H%M%S"),
                              int(time.time() * 1000) % 1000, _JOB_SEQ[0])


def add_job(title: str, when: dict, action: str = "remind",
            payload: str = "") -> dict:
    """新增任务。action: remind（提醒）/ skill（跑技能）/ notify（推手机）/ agent（自动组队）。"""
    title = (title or "").strip()[:80] or "未命名任务"
    job = {
        "id": _new_job_id(),
        "title": title,
        "action": action or "remind",
        "payload": (payload or "").strip()[:2000],
        "when": when or {"kind": "daily", "time": "09:00"},
        "enabled": True,
        "created": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "last_run": "", "next_run": "", "runs": 0, "last_result": "",
    }
    nf = next_fire(job)
    job["next_run"] = nf.strftime("%Y-%m-%d %H:%M") if nf else ""
    with _LOCK:
        jobs = _load()
        jobs.append(job)
        _save(jobs)
    return job


def remove_job(job_id: str) -> bool:
    with _LOCK:
        jobs = _load()
        keep = [j for j in jobs if j.get("id") != job_id]
        if len(keep) == len(jobs):
            return False
        _save(keep)
        return True


def toggle_job(job_id: str, enabled: Optional[bool] = None) -> Optional[dict]:
    with _LOCK:
        jobs = _load()
        for j in jobs:
            if j.get("id") == job_id:
                j["enabled"] = (not j.get("enabled", True)) if enabled is None else bool(enabled)
                nf = next_fire(j)
                j["next_run"] = nf.strftime("%Y-%m-%d %H:%M") if nf else ""
                _save(jobs)
                return j
    return None


def find_job(keyword: str) -> Optional[dict]:
    """按序号（1 起）或标题关键词找任务——用户说「取消吃药提醒」时用得上。"""
    kw = (keyword or "").strip()
    if not kw:
        return None
    jobs = _load()
    if kw.isdigit():
        i = int(kw) - 1
        if 0 <= i < len(jobs):
            return jobs[i]
    for j in jobs:
        if kw in str(j.get("title") or ""):
            return j
    return None


# ---------------- 人类可读 ----------------
def describe(job: dict) -> str:
    """把一条任务说成人话，例如「每天 09:00 · 提醒我吃药」。"""
    when = job.get("when") or {}
    kind = when.get("kind")
    if kind == "daily":
        plan = "每天 %s" % (when.get("time") or "09:00")
    elif kind == "weekly":
        dow = int((when.get("dow") or [0])[0]) % 7
        plan = "每%s %s" % (WEEK_LABEL[dow], when.get("time") or "09:00")
    elif kind == "interval":
        mins = int(when.get("minutes") or 60)
        plan = ("每 %d 分钟" % mins) if mins < 60 else ("每 %g 小时" % (mins / 60.0))
    elif kind == "once":
        plan = "一次性 %s" % str(when.get("at") or "").replace("T", " ")
    else:
        plan = "计划未知"
    flag = "" if job.get("enabled", True) else "（已停用）"
    return "%s%s · %s" % (plan, flag, job.get("title") or "")


def list_text() -> str:
    """任务清单（给聊天界面看）。空列表时给引导语。"""
    jobs = list_jobs()
    if not jobs:
        return ("你还没给我定过定时任务。\n\n"
                "直接说人话就能定，例如：\n"
                "· 「每天早上 8 点半提醒我吃药」\n"
                "· 「每小时提醒我起来动一动」\n"
                "· 「明天下午 3 点提醒我开会」\n"
                "· 「每周一 9 点把本周计划整理一下」")
    lines = []
    for i, j in enumerate(jobs, 1):
        nxt = j.get("next_run") or "—"
        lines.append("%d. %s\n   ⏭ 下次：%s" % (i, describe(j), nxt))
    return "我记着这些定时任务（共 %d 条）：\n\n%s" % (len(jobs), "\n".join(lines))


# ---------------- 后台线程 ----------------
def start(on_fire: Callable[[dict], None], poll: int = POLL_SECONDS) -> bool:
    """拉起后台轮询线程。on_fire(job) 由调用方决定「到点了做什么」。

    重复调用安全（已在跑就直接返回 True）。线程是守护线程，
    主程序退出即随之结束，不需要手动 join。
    """
    global _thread, _on_fire
    with _LOCK:
        _on_fire = on_fire
        if _thread is not None and _thread.is_alive():
            return True
        _stop.clear()
        _thread = threading.Thread(target=_loop, args=(max(5, int(poll)),),
                                   name="pasm-scheduler", daemon=True)
        _thread.start()
    return True


def stop() -> None:
    _stop.set()


def running() -> bool:
    return _thread is not None and _thread.is_alive()


def _loop(poll: int) -> None:
    # 启动后先等 3 秒，别和界面初始化抢资源
    if _stop.wait(3):
        return
    while not _stop.is_set():
        try:
            _tick()
        except Exception:
            pass
        # 用 Event.wait 休眠（可被 stop() 立刻唤醒），不是 time.sleep 忙等
        if _stop.wait(poll):
            return


def _tick() -> None:
    now = datetime.now()
    with _LOCK:
        jobs = _load()
    fired: List[dict] = []
    dirty = False
    for j in jobs:
        if not j.get("enabled", True):
            continue
        when = j.get("when") or {}
        if when.get("kind") == "once":
            try:
                due = datetime.fromisoformat(str(when.get("at") or ""))
            except Exception:
                continue
            if due > now:
                continue
        else:
            try:
                base = datetime.fromisoformat(j["next_run"]) if j.get("next_run") else None
            except Exception:
                base = None
            if base is None:
                base = next_fire(j, now)
                if base:
                    j["next_run"] = base.strftime("%Y-%m-%d %H:%M")
                    dirty = True
                continue
            if base > now:
                continue
        # ---- 到点了 ----
        if (now - _due_of(j, now)).total_seconds() > CATCHUP_MINUTES * 60:
            # 关机太久：这次跳过，顺延下次（避免一开机刷屏）
            nf = next_fire(j, now)
            j["next_run"] = nf.strftime("%Y-%m-%d %H:%M") if nf else ""
            if when.get("kind") == "once":
                j["enabled"] = False
            dirty = True
            continue
        fired.append(j)

    for j in fired:
        try:
            if _on_fire:
                _on_fire(j)
        except Exception:
            pass
        j["last_run"] = now.strftime("%Y-%m-%dT%H:%M:%S")
        j["runs"] = int(j.get("runs") or 0) + 1
        if (j.get("when") or {}).get("kind") == "once":
            j["enabled"] = False          # 一次性任务跑完自动停用
            j["next_run"] = ""
        else:
            nf = next_fire(j, now)
            j["next_run"] = nf.strftime("%Y-%m-%d %H:%M") if nf else ""
        dirty = True

    if dirty or fired:
        with _LOCK:
            _save(jobs)


def _due_of(j: dict, now: datetime) -> datetime:
    """这条任务理论上的触发时刻（用于判断「迟到多久」）。"""
    when = j.get("when") or {}
    if when.get("kind") == "once":
        try:
            return datetime.fromisoformat(str(when.get("at") or ""))
        except Exception:
            return now
    try:
        return datetime.fromisoformat(j["next_run"]) if j.get("next_run") else now
    except Exception:
        return now


# ---------------- 自检 ----------------
def selftest() -> str:
    """离线自检：只验证解析与推算，不落盘、不触发。"""
    out = []
    cases = [
        ("每天早上8点半提醒我吃药", "daily 08:30"),
        ("每天9点提醒我喝水", "daily 09:00"),
        ("每小时提醒我动一动", "interval 60"),
        ("每30分钟提醒我眨眼", "interval 30"),
        ("每周一9点整理本周计划", "weekly 0 09:00"),
        ("明天下午3点开会", "once"),
        ("9月20日10点交材料", "once"),
    ]
    ok = 0
    for text, expect in cases:
        when, rest = parse_when(text)
        got = ""
        if when:
            if when["kind"] == "daily":
                got = "daily %s" % when["time"]
            elif when["kind"] == "interval":
                got = "interval %d" % when["minutes"]
            elif when["kind"] == "weekly":
                got = "weekly %d %s" % (when["dow"][0], when["time"])
            else:
                got = "once"
        flag = "OK " if got == expect else "BAD"
        if got == expect:
            ok += 1
        out.append("%s %-22s -> %-16s 余下=%r" % (flag, text, got, rest))
    out.append("结果：%d/%d 通过" % (ok, len(cases)))
    return "\n".join(out)


if __name__ == "__main__":
    print(selftest())
