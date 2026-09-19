"""live_data —— 实时数据查询（天气等），免 API key。

- 天气：open-meteo 地理编码 + 预报接口，无需 token，全球可用。
- 设计原则：查不到/网络失败要抛出**中文可读**的 RuntimeError，让上层如实告知用户，
  绝不编造天气数据（与"诚实守则"一致）。
"""
import json
import re
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


def _http_json(url: str, timeout: int = 20):
    req = urllib.request.Request(url, headers={"User-Agent": "PASM/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


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
    # 取"天气类关键词"之前那一段作为城市名：
    # 覆盖 X天气 / X气温 / X下雨 / X冷不冷 / X空气质量 等（旧版只认 天气/气温，会漏）
    _idx = -1
    for _kw in ("天气", "气温", "温度", "气候", "下雨", "下雪", "降雨", "降雪",
                "冷不冷", "热不热", "预报", "空气质量"):
        _i = q.find(_kw)
        if _i > 0 and (_idx < 0 or _i < _idx):
            _idx = _i
    city = q[:_idx] if _idx > 0 else ""
    if not city:
        _m = re.search(r"[一-龥A-Za-z·]{2,20}", q)
        city = _m.group(0) if _m else ""
    # 去掉"会/要/下/的"等尾缀虚词（"上海会下雨"→"上海"）
    city = re.sub(r"(会|要|觉得|感觉|有|是|下|的|今天|明天|后天)+$", "", city).strip()
    city = city.strip(" ?？!！。，,、~～")
    if not city or city in ("天气", "气温", "温度", "气候", "下雨", "下雪"):
        city = "北京"
    try:
        geo = _http_json(f"{_GEO_URL}?name={urllib.parse.quote(city)}&count=1&language=zh&format=json")
    except Exception as ex:
        raise RuntimeError("天气服务（地理编码）连接失败：" + str(ex))
    results = geo.get("results") or []
    if not results:
        raise RuntimeError(f"没找到城市「{city}」，换个写法或试试拼音。")
    loc = results[0]
    lat = loc["latitude"]
    lon = loc["longitude"]
    name = loc.get("name", city)
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
