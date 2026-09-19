"""connector_calendar —— 日历连接器（v0.28.x）
==============================================

对齐 WorkBuddy / OpenClaw 的「日历」连接器。刻意做成**标准 iCalendar (.ics)**，
不搞私有格式 —— 好处是：

- 导出的 `calendar.ics` 能直接拖进 Google 日历 / Outlook / 手机日历；
- 也能反过来把别人的 .ics 导入进来（`import_ics`）。

实现要点：
- **纯标准库手写 ICS**（读取 + 生成），不依赖 icalendar 包，安装包不变大；
- 支持 RRULE 重复（DAILY / WEEKLY / MONTHLY，含 INTERVAL / COUNT / UNTIL / BYDAY）
  —— 这样从 Google 导出的重复日程也能正确展开；
- 时间解析复用 `scheduler.parse_when`，用户说「明天下午3点开会」就能建日程。

与「定时提醒」的分工（别混）：
- **日历** = 记「什么时候有什么事」（可导出、可分享、可看一周安排）；
- **定时任务** = 让 PASM「到点自己动起来」（提醒 / 跑技能 / 推手机）。
"""
from __future__ import annotations

import os
import re
from datetime import date, datetime, timedelta
from typing import List, Optional, Tuple

try:
    from pasm_companion import DATA_DIR
except Exception:                                # 独立导入兜底（测试用）
    DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".pasmstudio_dev")

CAL_PATH = os.path.join(DATA_DIR, "calendar.ics")
PRODID = "-//PASM Studio//Calendar//CN"

_WEEKDAY_ICS = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
_WEEKDAY_CN = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


# ---------------- 时间解析 ----------------
def parse_dt(text: str) -> Tuple[Optional[datetime], str, str]:
    """自然语言 → (开始时间, 剩余文字, rrule)。

    rrule 非空表示这是重复日程（如「每周一9点开周会」）。
    解析不了返回 (None, 原文, "")。
    """
    raw = (text or "").strip()
    if not raw:
        return None, raw, ""
    try:
        import scheduler as SCH
    except Exception:
        SCH = None
    if SCH is None:
        return None, raw, ""
    when, rest = SCH.parse_when(raw)
    if not when:
        return None, raw, ""
    kind = when.get("kind")
    if kind == "once":
        try:
            return datetime.fromisoformat(when["at"]), rest, ""
        except Exception:
            return None, raw, ""
    if kind == "daily":
        h, m = SCH._hhmm(when.get("time"))
        base = datetime.now().replace(hour=h, minute=m, second=0, microsecond=0)
        if base <= datetime.now():
            base += timedelta(days=1)
        return base, rest, "FREQ=DAILY"
    if kind == "weekly":
        h, m = SCH._hhmm(when.get("time"))
        dow = int((when.get("dow") or [0])[0]) % 7
        base = datetime.now().replace(hour=h, minute=m, second=0, microsecond=0)
        base += timedelta(days=(dow - base.weekday()) % 7)
        if base <= datetime.now():
            base += timedelta(days=7)
        return base, rest, "FREQ=WEEKLY;BYDAY=" + _WEEKDAY_ICS[dow]
    return None, raw, ""          # interval 型不属于日历（那是「提醒」的活）


# ---------------- ICS 基础 ----------------
def _fold(line: str) -> str:
    """按 RFC5545 折行（每行不超过 75 字节，续行以空格开头）。"""
    data = line.encode("utf-8")
    if len(data) <= 75:
        return line
    out, cur = [], b""
    for ch in line:
        b = ch.encode("utf-8")
        if len(cur) + len(b) > 73:
            out.append(cur)
            cur = b""
        cur += b
    out.append(cur)
    return "\r\n ".join(x.decode("utf-8", "replace") for x in out)


def _unfold(text: str) -> List[str]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n[ \t]", "", text)
    return [ln for ln in text.split("\n") if ln.strip()]


def _esc(s: str) -> str:
    return (str(s or "").replace("\\", "\\\\").replace(";", "\\;")
            .replace(",", "\\,").replace("\n", "\\n"))


def _unesc(s: str) -> str:
    return (str(s or "").replace("\\n", "\n").replace("\\,", ",")
            .replace("\\;", ";").replace("\\\\", "\\"))


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y%m%dT%H%M%S")


def _parse_icstime(v: str) -> Optional[datetime]:
    """解析 ICS 时间值：20260915T150000 / 20260915T070000Z / 20260915。"""
    v = (v or "").strip()
    if not v:
        return None
    utc = v.endswith("Z")
    v = v.rstrip("Z")
    try:
        if "T" in v:
            dt = datetime.strptime(v, "%Y%m%dT%H%M%S")
        else:
            dt = datetime.strptime(v, "%Y%m%d")
    except Exception:
        return None
    if utc:
        dt = dt + (datetime.now() - datetime.utcnow())   # 粗略转本地（±几十秒误差可接受）
    return dt


# ---------------- 读取 ----------------
def read_events(path: str = "") -> List[dict]:
    """解析 .ics → 事件列表（原始，未按时间窗展开）。"""
    p = path or CAL_PATH
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            lines = _unfold(f.read())
    except Exception:
        return []
    events: List[dict] = []
    cur: Optional[dict] = None
    for ln in lines:
        up = ln.upper()
        if up.startswith("BEGIN:VEVENT"):
            cur = {}
            continue
        if up.startswith("END:VEVENT"):
            if cur:
                cur.setdefault("uid", "pasm-%d" % (len(events) + 1))
                st = _parse_icstime(cur.get("dtstart", ""))
                if st:
                    cur["start"] = st
                    en = _parse_icstime(cur.get("dtend", ""))
                    cur["end"] = en or (st + timedelta(minutes=int(cur.get("_mins") or 60)))
                    events.append(cur)
            cur = None
            continue
        if cur is None or ":" not in ln:
            continue
        key, _, val = ln.partition(":")
        k = key.split(";")[0].strip().upper()
        if k == "DTSTART":
            cur["dtstart"] = val.strip()
        elif k == "DTEND":
            cur["dtend"] = val.strip()
        elif k == "SUMMARY":
            cur["title"] = _unesc(val.strip())
        elif k == "DESCRIPTION":
            cur["note"] = _unesc(val.strip())
        elif k == "UID":
            cur["uid"] = val.strip()
        elif k == "RRULE":
            cur["rrule"] = val.strip()
        elif k == "X-PASM-MINUTES":
            cur["_mins"] = val.strip()
    return events


# ---------------- RRULE 展开 ----------------
def _rrule_parts(rrule: str) -> dict:
    out = {}
    for seg in (rrule or "").split(";"):
        if "=" in seg:
            k, _, v = seg.partition("=")
            out[k.strip().upper()] = v.strip()
    return out


def _expand(ev: dict, win_start: datetime, win_end: datetime) -> List[datetime]:
    """把一个事件在 [win_start, win_end] 窗内展开成若干开始时间。"""
    st: datetime = ev["start"]
    rr = ev.get("rrule") or ""
    if not rr:
        return [st] if win_start <= st <= win_end else []
    p = _rrule_parts(rr)
    freq = (p.get("FREQ") or "").upper()
    try:
        interval = max(1, int(p.get("INTERVAL") or 1))
    except Exception:
        interval = 1
    count = int(p["COUNT"]) if str(p.get("COUNT") or "").isdigit() else 0
    until = _parse_icstime(p.get("UNTIL", "")) if p.get("UNTIL") else None

    out: List[datetime] = []
    if freq == "WEEKLY":
        byday = [d.strip().upper() for d in (p.get("BYDAY") or "").split(",") if d.strip()]
        days = [_WEEKDAY_ICS.index(d) for d in byday if d in _WEEKDAY_ICS] or [st.weekday()]
        cur = win_start - timedelta(days=7 * interval)
        guard = 0
        while cur <= win_end and guard < 2000:
            guard += 1
            if cur.weekday() in days and cur >= st:
                cand = cur.replace(hour=st.hour, minute=st.minute,
                                   second=0, microsecond=0)
                if cand >= st and win_start <= cand <= win_end:
                    out.append(cand)
            cur += timedelta(days=1)
        out.sort()
    elif freq == "MONTHLY":
        bmd = p.get("BYMONTHDAY")
        day = int(bmd) if (bmd or "").isdigit() else st.day
        y, m = win_start.year, win_start.month
        guard = 0
        while guard < 400:
            guard += 1
            try:
                cand = st.replace(year=y, month=m, day=day, second=0, microsecond=0)
            except ValueError:
                cand = None
            if cand and cand >= st and win_start <= cand <= win_end:
                out.append(cand)
            if datetime(y, m, 1) > win_end:
                break
            m += interval
            while m > 12:
                m -= 12
                y += 1
        out.sort()
    else:                                   # 默认按 DAILY
        cand = st
        # 快进到窗口附近
        if cand < win_start:
            gap = (win_start - cand).days
            steps = gap // interval
            cand += timedelta(days=steps * interval)
        guard = 0
        while cand <= win_end and guard < 2000:
            guard += 1
            if cand >= st and win_start <= cand <= win_end:
                out.append(cand)
            cand += timedelta(days=interval)
    if count:
        out = out[:count]
    if until:
        out = [t for t in out if t <= until]
    return out


def list_events(days: int = 7, path: str = "") -> List[dict]:
    """未来 `days` 天内的日程（已展开重复），按时间升序。
    返回 [{title, start, end, note, uid, recurring}]。"""
    now = datetime.now().replace(second=0, microsecond=0)
    win_start = now - timedelta(minutes=1)
    win_end = now + timedelta(days=max(1, int(days)))
    out: List[dict] = []
    for ev in read_events(path):
        for st in _expand(ev, win_start, win_end):
            dur = ev["end"] - ev["start"]
            out.append({"title": ev.get("title") or "(无标题)",
                        "start": st, "end": st + dur,
                        "note": ev.get("note") or "",
                        "uid": ev.get("uid") or "",
                        "recurring": bool(ev.get("rrule"))})
    out.sort(key=lambda e: e["start"])
    return out


# ---------------- 写入 ----------------
def write_events(events: List[dict], path: str = "") -> str:
    """把事件列表整体写回 .ics（规范化重建，删除后不留残渣）。"""
    p = path or CAL_PATH
    now_stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:" + PRODID,
             "CALSCALE:GREGORIAN", "X-WR-CALNAME:PASM 日程"]
    for ev in events:
        st = ev.get("start")
        if not isinstance(st, datetime):
            continue
        en = ev.get("end") or (st + timedelta(minutes=60))
        mins = int((en - st).total_seconds() // 60) or 60
        lines += [
            "BEGIN:VEVENT",
            "UID:" + (ev.get("uid") or "pasm-%s" % _fmt(st)),
            "DTSTAMP:" + now_stamp,
            "DTSTART:" + _fmt(st),
            "DTEND:" + _fmt(en),
            "SUMMARY:" + _esc(ev.get("title") or "(无标题)"),
            "X-PASM-MINUTES:%d" % mins,
        ]
        if ev.get("note"):
            lines.append("DESCRIPTION:" + _esc(ev["note"]))
        if ev.get("rrule"):
            lines.append("RRULE:" + str(ev["rrule"]))
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    body = "\r\n".join(_fold(x) for x in lines) + "\r\n"
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(body)
    os.replace(tmp, p)
    return p


def add_event(title: str, start: datetime, minutes: int = 60,
              note: str = "", rrule: str = "", path: str = "") -> dict:
    """新增一条日程（保留原有全部事件）。返回新事件 dict。"""
    ev = {"uid": "pasm-%s-%d" % (_fmt(start), abs(hash(title)) % 10000),
          "title": (title or "").strip() or "(无标题)",
          "start": start,
          "end": start + timedelta(minutes=max(5, int(minutes or 60))),
          "note": (note or "").strip(),
          "rrule": (rrule or "").strip()}
    events = read_events(path)
    events.append(ev)
    write_events(events, path)
    return ev


def add_from_text(text: str, path: str = "") -> Tuple[bool, str]:
    """用户说人话 → 建日程。返回 (成功?, 回复文案)。"""
    start, rest, rrule = parse_dt(text)
    if not start:
        return False, ("我没听出这是什么时候。说得再具体点就行，例如：\n"
                       "· 「明天下午 3 点开会」\n· 「9月20日10点交材料」\n"
                       "· 「每周一 9 点开周会」（会自动建成重复日程）")
    title = (rest or "").strip() or "日程"
    ev = add_event(title, start, 60, rrule=rrule, path=path)
    tip = "（重复日程）" if rrule else ""
    return True, ("📅 记下了%s：**%s**\n· 时间：%s\n· 文件：`%s`\n\n"
                  "这个 .ics 可以直接导入 Google 日历 / 手机日历。"
                  % (tip, ev["title"], ev["start"].strftime("%Y-%m-%d %H:%M"),
                     os.path.basename(path or CAL_PATH)))


def remove_event(keyword: str, path: str = "") -> Tuple[bool, str]:
    """按标题关键词删除日程。"""
    kw = (keyword or "").strip()
    events = read_events(path)
    if not kw:
        return False, "你想删哪一条？说一下标题里的词就行，例如「删掉周会」。"
    keep = [e for e in events
            if kw not in str(e.get("title") or "") and kw != str(e.get("uid") or "")]
    if len(keep) == len(events):
        return False, "没找到标题含「%s」的日程。" % kw
    write_events(keep, path)
    return True, "🗑 已删掉 %d 条含「%s」的日程。" % (len(events) - len(keep), kw)


def import_ics(src: str, path: str = "") -> Tuple[bool, str]:
    """把外部 .ics（Google/Outlook 导出）合并进本日历。"""
    if not os.path.isfile(src or ""):
        return False, "找不到文件：%s" % src
    incoming = read_events(src)
    if not incoming:
        return False, "这个 .ics 里没有我能识别的日程。"
    have = read_events(path)
    seen = {str(e.get("uid") or "") for e in have}
    added = 0
    for e in incoming:
        if str(e.get("uid") or "") in seen:
            continue
        have.append(e)
        seen.add(str(e.get("uid") or ""))
        added += 1
    write_events(have, path)
    return True, "📥 已从 `%s` 导入 %d 条日程（共 %d 条）。" % (
        os.path.basename(src), added, len(have))


# ---------------- 给聊天界面用的文案 ----------------
def agenda_text(days: int = 7, path: str = "") -> str:
    evs = list_events(days, path)
    if not evs:
        return ("📅 未来 %d 天没有日程，挺清静的。\n\n"
                "想加就说一句：「明天下午 3 点开会」。"
                % max(1, int(days)))
    now = datetime.now()
    lines = ["📅 未来 %d 天有 %d 项安排：" % (max(1, int(days)), len(evs))]
    last_day = None
    for e in evs:
        d = e["start"].date()
        if d != last_day:
            delta = (d - now.date()).days
            tag = "今天" if delta == 0 else ("明天" if delta == 1 else _WEEKDAY_CN[d.weekday()])
            lines.append("\n**【%s %s】**" % (d.strftime("%m-%d"), tag))
            last_day = d
        star = " 🔁" if e["recurring"] else ""
        lines.append("· %s %s%s" % (e["start"].strftime("%H:%M"), e["title"], star))
    return "\n".join(lines)


def status_text(path: str = "") -> str:
    events = read_events(path)
    recurring = sum(1 for e in events if e.get("rrule"))
    return ("📅 日历连接器\n· 文件：`%s`\n· 日程总数：%d（其中重复 %d）\n"
            "· 格式：标准 iCalendar(.ics)，可直接导入手机/Google 日历"
            % (path or CAL_PATH, len(events), recurring))
