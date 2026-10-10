"""live_data —— 实时数据查询（天气等），免 API key。

- 天气：open-meteo 地理编码 + 预报接口，无需 token，全球可用。
- 设计原则：查不到/网络失败要抛出**中文可读**的 RuntimeError，让上层如实告知用户，
  绝不编造天气数据（与"诚实守则"一致）。
"""
import json
import re
import time
import urllib.parse
import urllib.request

_GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"
_FC_URL = "https://api.open-meteo.com/v1/forecast"

_WMO = {
    0: "晴", 1: "大致晴朗", 2: "局部多云", 3: "阴",
    45: "有雾", 48: "雾凇",
    51: "小毛毛雨", 53: "毛毛雨", 55: "大毛毛雨",
    56: "冻毛毛雨", 57: "强冻毛毛雨",
    61: "小雨", 63: "中雨", 65: "大雨",
    66: "冻雨", 67: "强冻雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "雪粒",
    80: "阵雨", 81: "强阵雨", 82: "暴雨",
    85: "阵雪", 86: "强阵雪",
    95: "雷阵雨", 96: "雷阵雨伴冰雹", 99: "强雷阵雨伴冰雹",
}


def _http_json(url: str, timeout: int = 20, attempts: int = 3):
    """带**重试**地取 JSON。

    实测（2026-10-10）：天气接口偶发 `SSL: UNEXPECTED_EOF_WHILE_READING`，
    一次抖动就让用户看到"查天气失败了" —— 重试两次基本都能过。
    """
    last: Exception | None = None
    for i in range(max(1, attempts)):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "PASM/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as ex:                                  # noqa: BLE001
            last = ex
            if i < attempts - 1:
                time.sleep(0.6 * (i + 1))
    raise last if last else RuntimeError("http json 失败")


#: 地点引导词（"我这是广东省佛山市"）—— 命中即取其后地名，优先级最高
_PLACE_HINTS = ("我这是", "这里是", "我住在", "我在", "定位在", "坐标是", "坐标",
                "城市是", "地方是", "地点是")

#: 行政后缀（用于生成"去后缀 / 补后缀"变体）
_ADMIN_SUFFIX = ("省", "市", "区", "县", "自治区", "自治州", "地区", "盟")


def _norm_city(x: str) -> str:
    """清洗地名片段：剥引导词、剥尾部虚词、剥标点。"""
    x = (x or "").strip(" \t?？!！。，,、；;·~～")
    for h in _PLACE_HINTS:
        if h in x:
            x = x.split(h, 1)[1]
    x = re.sub(r"(会|要|觉得|感觉|有|是|下|的|今天|明天|后天|冷|热)+$", "", x)
    return x.strip(" \t?？!！。，,、；;·~～")


def _looks_like_place(x: str) -> bool:
    """粗判候选像不像**地名**（挡掉"今天天气怎么样"这类兜底整句）。

    宁可漏（返回 False → 退回默认城市）也不误判 —— 把"天气市"当成城市去查，
    用户看到的就是莫名其妙的结果。
    """
    if not x or not (2 <= len(x) <= 20):
        return False
    _bad = ("天气", "气温", "温度", "气候", "下雨", "下雪", "降雨", "降雪", "预报",
            "空气", "怎么", "怎么样", "如何", "吗", "呢", "需要", "添加", "穿", "衣服",
            "帮", "请", "查", "搜", "问", "看", "知道", "我", "你", "他", "她")
    if any(w in x for w in _bad):
        return False
    return bool(re.search(r"[\u4e00-\u9fa5A-Za-z]", x))


def _city_candidates(q: str):
    """从用户原话生成城市候选（**按优先级去重**），供逐个尝试。

    为什么需要：真机（2026-10-10）说「今天天气怎么样…哦对，我这是广东省佛山市」，
    结果查到的是**北京**；说「广东，佛山」「广东省，佛山市」都报"没找到城市"。

    实测 open-meteo 地理编码的脾气（决定了下面每一步的顺序）：
      · "佛山"      → 命中**云南**的佛山镇（错！中国有多个同名地）
      · "佛山市"    → 命中广东佛山 ✓
      · "上海市"    → 首位是**美国伊利诺伊州**的同名小镇（所以查询时要挑 country_code=CN）
      · "广东省佛山市" / "广东，佛山" / "广东佛山" → 全部 NO RESULT

    ⇒ 策略：**拆分 + 补「市」优先 + 市段优先 + 抛掉非地名兜底**。
    """
    out = []

    def add(x):
        x = _norm_city(x)
        if _looks_like_place(x) and x not in out:
            out.append(x)

    def variants(x):
        """给一段文本生成"最可能被地理编码认出"的地名候选。"""
        x = _norm_city(x)
        if not x:
            return
        # ① 含分隔符（逗号/顿号/空格）→ 拆分后**倒序**递归（城市通常写在最后）
        parts = [p for p in re.split(r"[，,、\s]+", x) if p]
        if len(parts) > 1:
            for p in reversed(parts):
                variants(p)
            return
        # ② "广东省佛山市" 连写 → 按行政层级切开（**先市后省**）
        m2 = re.search(r"^(.{1,6}?(?:省|自治区))(.{2,12}?(?:市|自治州|地区|盟))$", x)
        if m2:
            variants(m2.group(2))
            variants(m2.group(1))
            return
        # ★ 裸名排前面：实测 open-meteo 对**裸地名**支持最好，加"市"反而常查不到：
        #   "上海"→ 上海市/PPLA ✓    "上海市"→ 美国同名镇 ✗
        #   "深圳"→ 广东 ✓           "深圳市"→ 空 ✗
        #   带后缀的变体留作后备（"佛山"裸名会命中云南村镇 PPL，"佛山市"才是广东 ✓）。
        base = re.sub(r"(%s)$" % "|".join(_ADMIN_SUFFIX), "", x)
        if base != x:                      # 已带行政后缀：去后缀裸名 → 原样 → 补市
            add(base)
            add(x)
            add(base + "市")
        else:                              # 裸名：裸名 → 补「市」
            add(x)
            add(x + "市")

    # ① 地点引导词后的地名（"我这是广东省佛山市"）—— 优先级最高
    for h in _PLACE_HINTS:
        i = q.find(h)
        if i >= 0:
            variants(q[i + len(h):])
            break
    # ② "天气"类关键词之前那一段（"北京天气怎么样"）
    _idx = -1
    for _kw in ("天气", "气温", "温度", "气候", "下雨", "下雪", "降雨", "降雪",
                "冷不冷", "热不热", "预报", "空气质量"):
        _i = q.find(_kw)
        if _i > 0 and (_idx < 0 or _i < _idx):
            _idx = _i
    if _idx > 0:
        variants(q[:_idx])
    # ③ 整段中文兜底
    _m = re.search(r"[\u4e00-\u9fa5A-Za-z·]{2,20}", q)
    if _m:
        variants(_m.group(0))
    return out


def get_weather(query: str, days: int = 3) -> str:
    """查询天气，返回中文可读文本；失败抛 RuntimeError（中文可读）。"""
    q = (query or "").strip()
    # 先去掉夹杂的时间词/客气话，避免"北京今天天气"被解析成城市"北京今天"
    _filler = ["今天", "明天", "后天", "大后天", "周末", "周几", "现在", "最近", "此刻",
               "当前", "未来", "这几天", "的", "一下", "查询", "请问", "我想知道",
               "看看", "怎么样", "如何", "查一下", "搜一下", "帮我", "我想", "知道",
               "附近", "吗", "呢", "查", "搜", "问"]
    for _f in _filler:
        q = q.replace(_f, " ")
    q = q.strip()

    # ★ 2026-10-10：城市名改成**多候选逐个试**。
    #   旧版只解析出一个候选，解析错就直接用"北京"兜底 ——
    #   结果用户明明说了"广东省佛山市"，却查到北京（极其像幻觉，用户会不信）。
    cands = _city_candidates(q)
    if not cands:
        cands = ["北京"]             # 用户**完全没提地名** → 这才用默认城市
    loc = None
    used = ""
    _ok_req = 0
    _last_ex = None
    # 从候选里找省名（"广东省" / "广东"），用于在多个同名地中挑对省份
    _PROVS = ("广东", "江苏", "浙江", "山东", "河南", "河北", "湖南", "湖北", "四川",
              "福建", "安徽", "江西", "陕西", "山西", "云南", "贵州", "广西", "辽宁",
              "吉林", "黑龙江", "内蒙古", "甘肃", "青海", "宁夏", "新疆", "西藏",
              "海南", "北京", "上海", "天津", "重庆")
    _prov = ""
    for _p in cands:
        if _p.endswith(("省", "自治区")):
            _prov = re.sub(r"(省|自治区)$", "", _p)
            break
        if _p in _PROVS:
            _prov = _p
            break

    def _is_cn(x):
        return str(x.get("country_code", "")).upper() == "CN"

    def _is_city(x):
        """是不是"城市级"地名。open-meteo 的 feature_code：
        PPLA / PPLA2 / PPLA3… = 行政中心（省 / 地级市），PPL = 普通村镇居民点。
        实测：「上海」→ PPLA ✓；云南那个「佛山」→ PPL ✗；河南「上海市场社区」→ PPL ✗。
        """
        return str(x.get("feature_code", "")).startswith("PPLA")

    def _nm(x):
        return re.sub(r"(%s)$" % "|".join(_ADMIN_SUFFIX), "",
                      str(x.get("name", "")))

    # ★ 打分制：把"哪个候选 + 哪条结果最像用户说的那个地方"算成分数，取最高。
    #   为什么不用"第一个命中就停"：单一判据都会被同名地骗到 ——
    #   广东佛山/云南佛山、上海市/美国上海市/河南上海市场社区……必须综合判。
    _best = None
    _best_s = -1
    _best_c = ""
    for _c in cands[:8]:
        try:
            _geo = _http_json("%s?name=%s&count=5&language=zh&format=json"
                              % (_GEO_URL, urllib.parse.quote(_c)))
        except Exception as ex:
            _last_ex = ex
            continue
        _ok_req += 1
        _res = _geo.get("results") or []
        if not _res:
            continue
        _base = re.sub(r"(%s)$" % "|".join(_ADMIN_SUFFIX), "", _c)
        _municipal = _c.endswith(("市", "区", "县"))     # 用户明确写了行政后缀
        for _x in _res:
            _s = 0
            if _is_cn(_x):
                _s += 1                                  # 中国优先
            if str(_x.get("name", "")) == _c or _nm(_x) == _base:
                _s += 2                                  # 名字对得上
            if _is_city(_x):
                _s += 2                                  # 行政中心级（排开同名村镇）
            if _prov and _prov in str(_x.get("admin1", "")):
                _s += 4                                  # 省份对得上（最强线索）
            if _municipal and _nm(_x) == _base:
                _s += 1                                  # 用户写了"市" → 更可能就是城市
            if _s > _best_s:
                _best_s, _best, _best_c = _s, _x, _c
    if _best is not None:
        loc, used = _best, _best_c
    if loc is None:
        if _ok_req == 0 and _last_ex is not None:
            raise RuntimeError("天气服务（地理编码）连接失败：" + str(_last_ex))
        # 提了地名却查不到 → **如实告知**，绝不偷偷换成别的城市
        raise RuntimeError("没找到城市「%s」，换个写法或试试拼音。" % cands[0])
    lat = loc["latitude"]
    lon = loc["longitude"]
    name = loc.get("name", used)
    country = loc.get("country") or ""
    admin = loc.get("admin1") or ""
    try:
        fc = _http_json(
            f"{_FC_URL}?latitude={lat}&longitude={lon}"
            f"&current=temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m"
            f"&daily=weather_code,temperature_2m_max,temperature_2m_min"
            f"&timezone=auto&forecast_days={max(1, min(7, days))}")
    except Exception as ex:
        raise RuntimeError("天气服务（预报）连接失败：" + str(ex))
    cur = fc.get("current") or {}
    lines = [f"📍 {name}{('，' + admin) if admin and admin != name else ''}"
             f"{('，' + country) if country else ''} 的天气："]
    t = cur.get("temperature_2m")
    code = cur.get("weather_code")
    hum = cur.get("relative_humidity_2m")
    wind = cur.get("wind_speed_10m")
    lines.append(f"🌡 当前气温 {t}°C，{_WMO.get(code, '未知')}"
                 + (f"，湿度 {hum}%" if hum is not None else "")
                 + (f"，风速 {wind} km/h" if wind is not None else ""))
    daily = fc.get("daily") or {}
    d_times = daily.get("time") or []
    d_codes = daily.get("weather_code") or []
    d_max = daily.get("temperature_2m_max") or []
    d_min = daily.get("temperature_2m_min") or []
    if d_times:
        lines.append("未来几天：")
        for i in range(min(len(d_times), max(1, min(7, days)))):
            lines.append(f"  · {d_times[i][5:]}: {_WMO.get(d_codes[i] if i < len(d_codes) else 0, '未知')}"
                         f" {d_min[i] if i < len(d_min) else '?'}~{d_max[i] if i < len(d_max) else '?'}°C")
    return "\n".join(lines)
