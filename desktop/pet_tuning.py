# -*- coding: utf-8 -*-
"""pet_tuning —— 小人观感调参（v0.30.9）。

为什么单独成模块
----------------
「飞机多大 / 尾气多浓 / 飞多高」这三个值要在**三处**被读到：
  ① 聊天页头像（`pasm_companion`）
  ② 桌面浮窗头像（`pasm_pet` 的飞天路径）
  ③ 设置面板（`SettingsDialog` 的「小人」页）

如果各自写一份常量，改了一处忘另一处就会漂移 —— 而且症状是**看不出错**的
（"明明调到最大了，浮窗上的飞机还是小的"）。所以这里做**单一来源**：
`pet_avatar` 每次绘制前读一次，设置面板保存时 `load_from_cfg()` 覆盖，
桌面浮窗启动时读一次 `config.json`。

读取优先级
----------
`load_from_cfg(dict)`（设置面板保存）>  `config.json`  >  内置默认值。

诚实边界
--------
这三个都是**观感参数**：任何读取/解析失败一律退回默认值，**绝不抛异常**
（不该因为它们让小人画不出来）。越界值一律钳制到区间内，不报错也不静默变成 0。
"""
from __future__ import annotations

import colorsys
import json
import logging
import os

#: 可调项：键 → (默认值, 最小, 最大, 短名, 说明)
#: 默认值 = 「三项都略增强」那一档（小志 2026-09-16 选定）。
#: 短名给设置面板当标签用 —— 别在 UI 里另写一份中文名（会漂移）。
KNOBS = {
    "pet_fly_scale": (1.25, 0.60, 1.30, "飞机大小",
                      "1.00 = 原始机体，1.25 = 大一圈（上限 1.30 刚好填满控件）"),
    "pet_trail": (1.00, 0.00, 2.00, "尾气浓淡",
                  "1.00 = 默认档：70 颗粒子 / 1.5 秒寿命，拖到 0 就不喷了"),
    "pet_fly_height": (0.03, 0.01, 0.15, "飞天高度",
                       "巡航高度占屏幕高度的比例，0.03 = 贴着屏幕上沿飞"),
    # —— v0.30.11 #12 形象 DIY：配色三旋钮 + 头饰开关 ——
    # 配色是**对现有皮肤调色板做 HSL 变换**（不是换皮肤）：
    # 默认 0 / 1.0 / 1.0 是单位变换 → 出厂观感一模一样（零回归）。
    "pet_hue":    (0.00, 0.00, 1.00, "色相",
                   "0% = 原色；拖动给整个小人换一个色系（0~100% 绕色环一圈）"),
    "pet_sat":    (1.00, 0.00, 1.80, "饱和度",
                   "100% = 原色；0% = 灰阶，越高越鲜艳"),
    "pet_bright": (1.00, 0.60, 1.40, "明度",
                   "100% = 原色；越高越亮、越低越暗"),
    "pet_acc_ears":    (1.00, 0.00, 1.00, "头饰（耳/角）",
                        "关掉就不画头上的耳/角（2D 猫耳 / 3D 双角）"),
    "pet_acc_antenna": (1.00, 0.00, 1.00, "全息天线",
                        "头顶的天线杆 + 能量球（2D / 3D 都生效）"),
}

#: 0/1 开关型项 —— 设置面板渲染成**勾选框**（不是滑块），值仍是 0.0 / 1.0。
#: 放在这里当唯一来源：UI 只认这个集合，不许在界面里另写一份键名。
TOGGLES = frozenset({"pet_acc_ears", "pet_acc_antenna"})

#: 尾气基准（v0.30.9 按小志「更浓更明显」上调）。旧值：44 颗 / 1.05s / 概率 0.75。
TRAIL_BASE_CAP = 70
TRAIL_BASE_LIFE = 1.5
TRAIL_BASE_PROB = 0.85

_CUR: dict = {}
_LOADED = False


def defaults() -> dict:
    """出厂默认值（深拷贝，调用方可随便改）。"""
    return {k: v[0] for k, v in KNOBS.items()}


def clamp(key: str, val) -> float:
    """钳制到区间内；坏值（None/NaN/字符串）退回默认值。"""
    spec = KNOBS.get(key)
    if spec is None:
        return 0.0
    lo, hi, dflt = spec[1], spec[2], spec[0]
    try:
        x = float(val)
    except (TypeError, ValueError):
        return dflt
    if x != x:                       # NaN（float('nan') 不等于自己）
        return dflt
    return max(lo, min(hi, x))


def _config_path() -> str:
    """config.json 路径 —— 与 `pasm_companion.DATA_DIR` **同一来源**（logsetup）。"""
    try:
        import logsetup
        return os.path.join(logsetup.data_dir(), "config.json")
    except Exception:                      # noqa: BLE001  拿不到就算了，用默认值
        base = os.environ.get("PASM_STUDIO_DIR") or os.environ.get("APPDATA") or ""
        return os.path.join(base, "PASMStudio", "config.json") if base else ""


def ensure_loaded() -> None:
    """首次使用时从 config.json 读一次（之后只有显式 load_from_cfg 才会变）。"""
    global _LOADED
    if _LOADED:
        return
    cfg = {}
    try:
        p = _config_path()
        if p and os.path.isfile(p):
            with open(p, encoding="utf-8") as f:
                cfg = json.load(f) or {}
    except Exception as ex:                # noqa: BLE001 观感参数，读不到就用默认
        logging.debug("pet_tuning 读配置失败（用默认值）: %r", ex)
    load_from_cfg(cfg)


def load_from_cfg(cfg) -> dict:
    """从配置字典同步（设置面板保存 / 启动时读 config.json 后调用）。"""
    global _LOADED
    d = defaults()
    if isinstance(cfg, dict):
        for k in KNOBS:
            if k in cfg:
                d[k] = clamp(k, cfg[k])
    _CUR.clear()
    _CUR.update(d)
    _LOADED = True
    return dict(_CUR)


def reset() -> dict:
    """清空内存状态 → **下次读取会重新去读 config.json**。

    ⚠️ 这里刻意**不**填默认值也不置 `_LOADED`（自检当场抓到过这个坑）：
    第一版写成 `return load_from_cfg({})`，等于"用默认值标记为已加载"，
    于是 `reset()` 之后再也不会读 config.json —— 用户改的设置在内存里永远不生效。
    """
    global _LOADED
    _CUR.clear()
    _LOADED = False
    return defaults()


def all_values() -> dict:
    ensure_loaded()
    return dict(_CUR)


def v(key: str) -> float:
    """当前生效值（未知键返回 0.0，不抛）。"""
    ensure_loaded()
    if key not in KNOBS:
        return 0.0
    return float(_CUR.get(key, KNOBS[key][0]))


# ---- 语义化读取（业务侧只认这些，别在别处手算）----

def fly_scale() -> float:
    """飞机形态的额外缩放系数（1.0 = 原始机体）。"""
    return v("pet_fly_scale")


def fly_height() -> float:
    """巡航高度 = 可用屏高 × 该比例。"""
    return v("pet_fly_height")


def trail() -> float:
    """尾气浓淡倍数（0 = 不喷尾气）。"""
    return v("pet_trail")


def trail_cap() -> int:
    """同时存在的尾气粒子上限。"""
    return int(round(TRAIL_BASE_CAP * trail()))


def trail_life() -> float:
    """粒子寿命（秒）—— 浓的时候拖尾更长。

    线性展开：0 → 1.05s、1.0 → 1.5s、2.0 → 1.95s。
    ⚠️ 别写成 `BASE * (0.55 + 0.45 * min(1.0, L))` —— 那个 `min` 会把
    1.0 以上全压成同一值（自检当场抓到"调到 2.0 寿命不变"）。
    """
    return TRAIL_BASE_LIFE * (0.70 + 0.30 * trail())


def trail_prob() -> float:
    """每帧（50ms）喷一颗的概率。"""
    return min(1.0, TRAIL_BASE_PROB * trail())


def trail_size() -> float:
    """粒子半径倍数（浓的时候更大更明显）。"""
    return 0.80 + 0.20 * trail()


# ---- v0.30.11 #12 形象 DIY：配色 + 头饰 ----

def hue() -> float:
    """色相偏移（0..1，0 = 原色）。"""
    return v("pet_hue")


def sat() -> float:
    """饱和度倍数（1.0 = 原色，0 = 灰阶）。"""
    return v("pet_sat")


def bright() -> float:
    """明度倍数（1.0 = 原色）。"""
    return v("pet_bright")


def acc_on(key: str) -> bool:
    """头饰开关。未知键一律 True —— 未知不该让东西莫名消失。"""
    if key not in TOGGLES:
        return True
    return v(key) >= 0.5


def look_is_identity() -> bool:
    """当前配色是否等于"不动"（用于跳过整条变换，保出厂观感零回归）。"""
    return (abs(hue()) < 1e-9 and abs(sat() - 1.0) < 1e-9
            and abs(bright() - 1.0) < 1e-9)


def transform_rgb(rgb):
    """对 0..1 的 (r, g, b) 施加「色相 / 饱和度 / 明度」变换。

    2D（QColor）与 3D（装甲四色）共用这一个函数，保证两条渲染路径**同色**。
    默认值（0 / 1.0 / 1.0）下恒等返回 —— 出厂观感一个像素都不变。
    任何异常都退回原色（观感参数绝不能让小人画不出来）。
    """
    if look_is_identity():
        return rgb
    try:
        hh, ss, vv = colorsys.rgb_to_hsv(float(rgb[0]), float(rgb[1]),
                                         float(rgb[2]))
        hh = (hh + hue()) % 1.0
        ss = 0.0 if ss * sat() < 0.0 else min(1.0, ss * sat())
        vv = 0.0 if vv * bright() < 0.0 else min(1.0, vv * bright())
        return colorsys.hsv_to_rgb(hh, ss, vv)
    except Exception:                       # noqa: BLE001
        return rgb


# ---------------------------------------------------------------- 自检

def _selftest() -> int:
    fails = []

    def check(name, cond, got=None):
        if cond:
            print("  [OK] %s" % name)
        else:
            fails.append(name)
            print("  [FAIL] %s | got=%r" % (name, got))

    print("== pet_tuning 自检 ==")
    import tempfile

    # ① 默认值就在区间内（别出现"默认值自己被钳掉"这种自相矛盾）
    d = defaults()
    check("默认值项数与 KNOBS 一致", len(d) == len(KNOBS), sorted(d))
    for k, val in d.items():
        lo, hi = KNOBS[k][1], KNOBS[k][2]
        check("默认值 %s 在区间内" % k, lo <= val <= hi, (val, lo, hi))

    # ② 钳制：越界收紧、坏值兜底（这是它唯一的职责，必须钉死）
    reset()
    check("超上限被钳", load_from_cfg({"pet_fly_scale": 9})["pet_fly_scale"] == 1.30)
    check("超下限被钳", load_from_cfg({"pet_fly_scale": -5})["pet_fly_scale"] == 0.60)
    check("字符串数字可用", load_from_cfg({"pet_trail": "1.5"})["pet_trail"] == 1.5)
    check("坏字符串退回默认", load_from_cfg({"pet_trail": "abc"})["pet_trail"] == 1.00)
    check("None 退回默认", load_from_cfg({"pet_trail": None})["pet_trail"] == 1.00)
    check("NaN 退回默认（不是 0）",
          load_from_cfg({"pet_trail": float("nan")})["pet_trail"] == 1.00)
    check("非 dict 不崩", load_from_cfg("不是字典")["pet_trail"] == 1.00)
    check("未知键被忽略", "pet_zzz" not in load_from_cfg({"pet_zzz": 1}))
    check("未知键 v() 返回 0 不抛", v("pet_zzz") == 0.0)

    # ③ 语义函数随浓度单调（尾气越浓：粒子越多、活得越久、喷得越勤）
    reset()
    load_from_cfg({"pet_trail": 0.0})
    check("浓度 0 → 不喷粒子", trail_cap() == 0, trail_cap())
    load_from_cfg({"pet_trail": 1.0})
    c1, l1, p1 = trail_cap(), trail_life(), trail_prob()
    check("浓度 1.0 → 70 颗（增强档）", c1 == 70, c1)
    check("浓度 1.0 → 寿命 1.5s", abs(l1 - 1.5) < 1e-9, l1)
    load_from_cfg({"pet_trail": 2.0})
    check("浓度 2.0 → 粒子更多", trail_cap() > c1, trail_cap())
    check("浓度 2.0 → 寿命更长", trail_life() > l1, trail_life())
    check("浓度 2.0 → 喷得更勤", trail_prob() >= p1, trail_prob())
    check("概率被压在 1.0 以内", trail_prob() <= 1.0, trail_prob())

    # ④ 从 config.json 读：写一份临时配置，确认真的读到了
    tmp = tempfile.mkdtemp(prefix="pt_")
    old_env = os.environ.get("PASM_STUDIO_DIR")
    os.environ["PASM_STUDIO_DIR"] = tmp
    try:
        with open(os.path.join(tmp, "config.json"), "w", encoding="utf-8") as f:
            json.dump({"pet_fly_scale": 0.9, "pet_fly_height": 0.08}, f)
        reset()
        check("从 config.json 读到飞机大小", abs(fly_scale() - 0.9) < 1e-9, fly_scale())
        check("从 config.json 读到飞天高度", abs(fly_height() - 0.08) < 1e-9, fly_height())
        check("未写的那项用默认值", abs(trail() - 1.00) < 1e-9, trail())
        # 坏 JSON 不能崩（观感参数不该阻断启动）
        with open(os.path.join(tmp, "config.json"), "w", encoding="utf-8") as f:
            f.write("{坏掉的 json")
        reset()
        check("config.json 坏掉 → 退回默认且不抛",
              abs(fly_scale() - 1.25) < 1e-9, fly_scale())
    finally:
        if old_env is None:
            os.environ.pop("PASM_STUDIO_DIR", None)
        else:
            os.environ["PASM_STUDIO_DIR"] = old_env
        reset()

    # ⑤ v0.30.11 #12：形象 DIY —— 默认恒等、可变换、头饰开关
    reset()
    check("配色默认是恒等变换（零回归）", look_is_identity())
    check("恒等时 transform_rgb 原样返回",
          transform_rgb((0.2, 0.5, 0.9)) == (0.2, 0.5, 0.9))
    load_from_cfg({"pet_sat": 0.0})
    _g = transform_rgb((0.2, 0.5, 0.9))
    check("饱和 0 → 灰阶（三通道相等）",
          abs(_g[0] - _g[1]) < 1e-6 and abs(_g[1] - _g[2]) < 1e-6, _g)
    check("饱和 0 不再是恒等", not look_is_identity())
    reset()
    load_from_cfg({"pet_hue": 0.5})
    _h = transform_rgb((1.0, 0.0, 0.0))
    check("色相 +50% → 红变青", _h[2] > 0.9 and _h[0] < 0.05, _h)
    reset()
    load_from_cfg({"pet_acc_ears": 0.0, "pet_acc_antenna": 0.0})
    check("头饰开关关 → acc_on False", not acc_on("pet_acc_ears")
          and not acc_on("pet_acc_antenna"))
    check("未知键 acc_on 默认 True（不藏东西）", acc_on("pet_acc_zzz"))
    reset()
    check("默认头饰都开着", acc_on("pet_acc_ears") and acc_on("pet_acc_antenna"))
    check("TOGGLES 都在 KNOBS 里", set(TOGGLES) <= set(KNOBS), sorted(TOGGLES))

    print("-" * 46)
    if fails:
        print("自检失败 %d 项：%s" % (len(fails), "；".join(fails)))
        return 1
    print("自检通过（0 失败）")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_selftest())
