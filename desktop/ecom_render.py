# -*- coding: utf-8 -*-
"""ecom_render.py —— 电商图集**真实合成**（PySide6 QPainter，零新依赖，v0.31.27）
================================================================================

`ecom.py` 只产出**方案与几何**（裁哪块、放哪、写什么字），本模块负责把它变成
**真像素**：从用户喂的实拍图裁切、放大、叠字、拼长图，最后落成一堆可直接上传的
PNG。全程只用 PySide6（项目已有依赖），**不引 Pillow**。

## 为什么用 QPainter 而不是 Pillow

  · Pillow 是**新依赖** → 要改 requirements、要进 `build_common.HIDDEN_IMPORTS`、
    还给跨平台打包多一份风险；而 PySide6 已经是硬依赖。
  · QImage/QPainter 天生支持中文排版（字体/行高/换行）、圆角、渐变、缩放插值，
    正是电商主图需要的那些。
  ⚠️ 只用 `QImage` **不用 QPixmap** —— 本模块会在 worker 线程里跑，QPixmap 非线程安全。

## 三个真本事（都不是"画个色块糊过去"）

1. **裁切放大**：按方案的相对坐标从**原图**取样 → 无级放大 → 居中裁成方图。
   细节图/局部图因此是**真实拍到的画面**，不是 AI 编的。
2. **自动挑细节区域**：`pick_detail_boxes()` 把图缩到 96×96，按 3×3 网格算
   **高频能量**（相邻像素差），挑信息量最大的几格当细节区。比"永远裁正中"专业得多。
3. **简易抠图**：`auto_matte()` 从四边做**种子扩散**（BFS），把与背景同色且
   **连通到边界**的像素置为透明。只用于白底主图，且**四角颜色必须接近**才敢动手；
   抠不动就原图放上去并如实说明（绝不硬抠出一堆白洞）。

## 诚实边界（改之前先读）

- 没有实拍图 → 返回 `ok=False` + 原因，**一张都不生成**（主图不能凭空捏）。
- 详情长图里没有图的那几屏画**占位块并写明「待生成」**，不画假图。
- 抠图/裁切失败一律**降级到"原图直接放"**并记进 `notes`，不让整个流程失败。
"""
from __future__ import annotations

import os
import re
import time
from collections import deque
from typing import Dict, List, Optional, Tuple

SUPPORTED_EXT = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff")

#: 中文字体候选（Qt6 的 setFamilies 会按序回退；全都没有就用系统默认）
FONT_CANDIDATES = ("Microsoft YaHei", "微软雅黑", "PingFang SC", "Hiragino Sans GB",
                   "Noto Sans CJK SC", "Source Han Sans SC", "WenQuanYi Micro Hei",
                   "SimHei", "Sans Serif")

#: 白底主图里产品占画幅的比例（0.78 ≈ 四周各留 11% 白边）
_PRODUCT_RATIO = 0.78
#: 详情长图单张高度上限（超过平台会压缩；这里只提示，不擅自缩水）
DETAIL_MAX_H = 15000


# --------------------------------------------------------------------------
# Qt 依赖（惰性导入：本模块可被 import，真正用时才需要 PySide6）
# --------------------------------------------------------------------------
def _qt():
    from qt_compat import (QImage, QPainter, QColor, QFont, QFontMetrics, QPen,
                           QBrush, QLinearGradient, QRect, QRectF, QPoint, QSize,
                           Qt, QGuiApplication)
    return dict(QImage=QImage, QPainter=QPainter, QColor=QColor, QFont=QFont,
                QFontMetrics=QFontMetrics, QPen=QPen, QBrush=QBrush,
                QLinearGradient=QLinearGradient, QRect=QRect, QRectF=QRectF,
                QPoint=QPoint, QSize=QSize, Qt=Qt, QGuiApplication=QGuiApplication)


_APP = []


def available() -> bool:
    """Qt 能不能用（打包漏了 PySide6 或极简环境里返回 False → 调用方降级 HTML）。"""
    try:
        _qt()
        return True
    except Exception:                            # noqa: BLE001
        return False


def _qapp():
    """确保存在一个 QGuiApplication（QImage 画字需要）。已有实例直接复用。"""
    q = _qt()
    app = q["QGuiApplication"].instance()
    if app is None:
        app = q["QGuiApplication"]([])
        _APP.append(app)                          # 持有引用，防被 GC
    return app


def _font(size: int, bold: bool = False):
    q = _qt()
    f = q["QFont"]()
    f.setFamilies(list(FONT_CANDIDATES))
    f.setPixelSize(int(size))
    f.setBold(bool(bold))
    return f


def _c(spec) -> "QColor":
    q = _qt()
    return spec if isinstance(spec, q["QColor"]) else q["QColor"](str(spec))


# --------------------------------------------------------------------------
# 读写
# --------------------------------------------------------------------------
def is_image(path: str) -> bool:
    return os.path.splitext(str(path or ""))[1].lower() in SUPPORTED_EXT


def load_image(path: str):
    """读图（失败返回 None，不抛）。"""
    q = _qt()
    img = q["QImage"](str(path or ""))
    return img if not img.isNull() else None


def save_image(img, path: str) -> bool:
    """落 PNG（先写 .tmp 再换名，避免半截文件）。"""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp.png"
        ok = bool(img.save(tmp, "PNG"))
        if not ok:
            return False
        try:
            os.replace(tmp, path)
        except OSError:
            return os.path.isfile(tmp)
        return os.path.isfile(path) and os.path.getsize(path) > 0
    except Exception:                            # noqa: BLE001
        return False


def blank(w: int, h: int, fill="#ffffff"):
    q = _qt()
    img = q["QImage"](int(w), int(h), q["QImage"].Format.Format_RGBA8888)
    img.fill(_c(fill))
    return img


# --------------------------------------------------------------------------
# 简易抠图（四边种子扩散）
# --------------------------------------------------------------------------
def _bg_color_bytes(buf: bytearray, w: int, h: int) -> Optional[Tuple[int, int, int]]:
    """四角 + 四边中点的平均色（RGBA 缓冲，内存序 = R,G,B,A）。四角差异过大 → None。"""
    pts = [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1),
           (w // 2, 0), (w // 2, h - 1), (0, h // 2), (w - 1, h // 2)]
    cols = []
    for x, y in pts:
        i = (y * w + x) * 4
        cols.append((buf[i], buf[i + 1], buf[i + 2]))
    r = sum(c[0] for c in cols) // len(cols)
    g = sum(c[1] for c in cols) // len(cols)
    b = sum(c[2] for c in cols) // len(cols)
    for c in cols:                               # 四角不接近 → 不是纯色背景，别抠
        if max(abs(c[0] - r), abs(c[1] - g), abs(c[2] - b)) > 42:
            return None
    return r, g, b


def auto_matte(src, tol: int = 30, max_side: int = 1000):
    """把与边界连通的背景色像素置为透明。返回 (QImage, applied: bool)。

    只对**纯色背景**（四角颜色接近）生效；否则原样返回并 applied=False。
    这是"够用就好"的实现：电商实拍九成是白/灰底，够用；复杂背景交给专业工具，
    **不硬抠**（硬抠出来的白洞比不抠糟得多）。
    """
    q = _qt()
    try:
        img = src
        if max(img.width(), img.height()) > max_side:
            img = img.scaled(max_side, max_side, q["Qt"].AspectRatioMode.KeepAspectRatio,
                             q["Qt"].TransformationMode.SmoothTransformation)
        img = img.convertToFormat(q["QImage"].Format.Format_RGBA8888)
        w, h = img.width(), img.height()
        if w < 8 or h < 8:
            return src, False
        bpl = img.bytesPerLine()
        mv = img.constBits()
        raw = bytearray(mv[: bpl * h])
        # 去掉行末 padding，整理成紧凑的 w*h*4
        buf = bytearray(w * h * 4)
        for y in range(h):
            buf[y * w * 4: (y + 1) * w * 4] = raw[y * bpl: y * bpl + w * 4]
        bg = _bg_color_bytes(buf, w, h)
        if bg is None:
            return src, False
        r, g, b = bg
        t2 = tol * tol * 3
        seen = bytearray(w * h)
        dq = deque()
        for x in range(w):
            dq.append((x, 0))
            dq.append((x, h - 1))
        for y in range(h):
            dq.append((0, y))
            dq.append((w - 1, y))
        cleared = 0
        while dq:
            x, y = dq.popleft()
            if x < 0 or y < 0 or x >= w or y >= h:
                continue
            k = y * w + x
            if seen[k]:
                continue
            i = k * 4
            dr, dg, db = buf[i] - r, buf[i + 1] - g, buf[i + 2] - b
            if dr * dr + dg * dg + db * db > t2:
                continue
            seen[k] = 1
            buf[i + 3] = 0
            cleared += 1
            dq.append((x + 1, y))
            dq.append((x - 1, y))
            dq.append((x, y + 1))
            dq.append((x, y - 1))
        # 背景占比太小（<3%）→ 说明判据没咬合，宁可原样放
        if cleared < w * h * 0.03:
            return src, False
        out = q["QImage"](bytes(buf), w, h, w * 4,
                          q["QImage"].Format.Format_RGBA8888).copy()
        return out, True
    except Exception:                            # noqa: BLE001
        return src, False


# --------------------------------------------------------------------------
# 专业去底（桥到 imgmatte；不可用时退回简易法并如实标注）
# --------------------------------------------------------------------------
def _to_np(img):
    """QImage → (H,W,4) uint8 numpy 数组（注意行 stride）。取不到 numpy 返回 None。"""
    try:
        import numpy as n
    except Exception:                                        # noqa: BLE001
        return None
    q = _qt()
    img = img.convertToFormat(q["QImage"].Format.Format_RGBA8888)
    w, h = img.width(), img.height()
    bpl = img.bytesPerLine()
    buf = bytes(img.constBits())[: bpl * h]
    a = n.frombuffer(buf, n.uint8).reshape(h, bpl)[:, : w * 4].reshape(h, w, 4)
    return a.copy()


def pro_matte(src, keep_shadow: bool = True):
    """专业级去底。返回 `(QImage, info)`。

    info: `{applied, mode: "pro"|"simple", ...}` —— `mode` 一定要如实标出来：
    `simple` 代表"numpy 不在，退回四边同色洪水填充"，抠不动渐变底也没有
    去色溢出。用户有权知道拿到的白底图是哪一档做的。
    """
    try:
        import imgmatte as IM
    except Exception:                                        # noqa: BLE001
        IM = None
    A = _to_np(src)
    if IM is None or A is None or not IM.available():
        m, ok = auto_matte(src)
        return m, {"applied": bool(ok), "mode": "simple",
                   "reason": "" if ok else "numpy 不可用 → 退回简易去底"}
    h, w = A.shape[:2]
    alpha, info = IM.matte(A.tobytes(), w, h, keep_shadow=keep_shadow)
    info = dict(info or {})
    info["mode"] = "pro"
    if not info.get("applied"):
        return src, info
    # 去色溢出：不除白边的话，白底主图上产品周围永远有一圈发白毛边
    rgba = IM.decontaminate(A.tobytes(), alpha, w, h, bg=(255, 255, 255))
    q = _qt()
    out = q["QImage"](rgba, w, h, w * 4,
                      q["QImage"].Format.Format_RGBA8888).copy()
    # 前景平均亮度：纯白/浅色产品在白底上会"消失"，需要极细描边（见 render_main_image）
    try:
        import numpy as n
        al = n.frombuffer(alpha, n.uint8).reshape(h, w).astype(n.float32) / 255.0
        solid = al > 0.85
        if solid.any():
            lum = (A[:, :, :3].astype(n.float32) @
                   n.array([0.299, 0.587, 0.114], n.float32))
            info["fg_luma"] = round(float(lum[solid].mean()), 1)
    except Exception:                                        # noqa: BLE001
        pass
    return out, info


def pro_matte_product(src, box=None, keep_shadow: bool = True):
    """**手持 / 复杂底**实拍的专业去底（桥到 imgmatte.matte_product）。

    与 `pro_matte` 的分工：`pro_matte` 走"四边多背景色建模 + shared matting"，
    要求背景相对干净；手持图（手紧贴产品、底色不止一种、还有文字水印）会被它
    判「背景过于复杂」整体拒绝，白底主图只能垫白画布——而淘宝/京东的搜索位与
    放大镜要的是**真白底**。`matte_product` 改用「产品框 + 肤色排除 + 背景色
    距离」三判据，并对不可靠的结果**诚实拒绝**。

    返回 `(QImage, info)`，`info.mode = "product"`。不可用时返回 `(src, info)`
    且 `info["applied"]=False`，`info["reason"]` 说清为什么不敢抠。
    """
    try:
        import imgmatte as IM
    except Exception:                                        # noqa: BLE001
        IM = None
    A = _to_np(src)
    if IM is None or A is None or not IM.available():
        return src, {"applied": False, "mode": "product",
                     "reason": "numpy 不可用"}
    h, w = A.shape[:2]
    alpha, info = IM.matte_product(A.tobytes(), w, h, box,
                                   keep_shadow=keep_shadow)
    info = dict(info or {})
    info["mode"] = "product"
    if not info.get("applied"):
        return src, info
    # 与 pro_matte 同一套去色溢出：不除白边，白底图上产品周围永远一圈发白毛边
    rgba = IM.decontaminate(A.tobytes(), alpha, w, h, bg=(255, 255, 255))
    q = _qt()
    out = q["QImage"](rgba, w, h, w * 4,
                      q["QImage"].Format.Format_RGBA8888).copy()
    try:
        import numpy as n
        al = n.frombuffer(alpha, n.uint8).reshape(h, w).astype(n.float32) / 255.0
        solid = al > 0.85
        if solid.any():
            lum = (A[:, :, :3].astype(n.float32) @
                   n.array([0.299, 0.587, 0.114], n.float32))
            info["fg_luma"] = round(float(lum[solid].mean()), 1)
    except Exception:                                        # noqa: BLE001
        pass
    return out, info


# --------------------------------------------------------------------------
# 自动挑细节区域
# --------------------------------------------------------------------------
def _energy_grid(src, grid: int = 3, sz: int = 96):
    """把图缩到 sz×sz，按 grid×grid 网格算**高频能量**（相邻像素差之和）。

    纯色格 ≈ 0，有纹理/文字/边缘的格明显更大。返回 grid×grid 的二维列表。
    """
    q = _qt()
    small = src.scaled(sz, sz, q["Qt"].AspectRatioMode.IgnoreAspectRatio,
                       q["Qt"].TransformationMode.SmoothTransformation)
    g = max(2, sz // max(2, grid))
    vals = [small.pixelColor(x, y).value() for y in range(sz) for x in range(sz)]
    cells = []
    for cy in range(grid):
        row = []
        for cx in range(grid):
            e = 0
            for y in range(cy * g, min((cy + 1) * g, sz - 1)):
                base = y * sz
                for x in range(cx * g, min((cx + 1) * g, sz - 1)):
                    v = vals[base + x]
                    e += abs(v - vals[base + x + 1]) + abs(v - vals[base + sz + x])
            row.append(e)
        cells.append(row)
    return cells


def pick_detail_boxes(src, n: int = 3, grid: int = 3, box_rel: float = 0.38) -> List[List[float]]:
    """按 3×3 网格算高频能量，挑信息量最大的 n 格 → 相对坐标框。

    判据：格内所有"右邻差 + 下邻差"之和。纯色格 ≈ 0，有纹理/文字的格明显更大。
    全图都平（纯色素材图）→ 返回空列表，调用方用 `ecom.build_crops` 的兜底值。

    ⚠️ 这个判据**分不清"产品"和"杂讯"**：手指皮肤的纹理、图片上的文字水印
    同样是高频。v0.31.30 起优先走 `pick_product_boxes`（视觉模型定位产品），
    本函数只作"没有云端视觉"时的兜底。
    """
    try:
        cells = _energy_grid(src, grid=grid)
    except Exception:                                    # noqa: BLE001
        return []
    flat = [(cells[cy][cx], cx, cy) for cy in range(grid) for cx in range(grid)]
    flat.sort(reverse=True)
    if not flat or flat[0][0] <= 0:
        return []
    out = []
    for _e, cx, cy in flat[:max(1, int(n))]:
        cxp = (cx + 0.5) / grid
        cyp = (cy + 0.5) / grid
        x = min(max(0.0, cxp - box_rel / 2), 1.0 - box_rel)
        y = min(max(0.0, cyp - box_rel / 2), 1.0 - box_rel)
        out.append([round(x, 3), round(y, 3), box_rel, box_rel])
    return out


def pick_product_boxes(src, n: int = 3, box=None,
                       pad: float = 0.14) -> List[List[float]]:
    """从**产品边界框**推导 n 个局部/细节图裁切区（v0.31.30，核心修复）。

    `box` = [x, y, w, h]（归一化，由 `percept.product_box` 问视觉模型得来）。
    为什么必须用它：真机实录（2026-10-14）用户喂了一张"手托项链"的实拍图，
    细节图裁出来**全是手指和皮肤**——因为"高频能量"判据选中了皮肤纹理和
    图片上的「2克拉」文字水印，产品本身一次都没入选。产品框直接从根上解决。

    推导规则：
      · 第 1 个 = 产品主体（框外扩 pad，保证轮廓与边缘工艺入镜）；
      · 其余 n-1 个 = 在产品框**内部**按高频能量找子区（②工艺/标识、③结构/底部），
        只收"中心落在产品框内"的格 → 不可能再拍到框外的手。
    box 非法 / 拿不到 → 返回 []，调用方回退到 `pick_detail_boxes`。
    """
    if not box:
        return []
    try:
        bx, by, bw, bh = [float(v) for v in list(box)[:4]]
    except Exception:                                    # noqa: BLE001
        return []
    if not (0.0 <= bx <= 1.0 and 0.0 <= by <= 1.0) or bw < 0.03 or bh < 0.03:
        return []
    x0 = max(0.0, bx - pad * bw)
    y0 = max(0.0, by - pad * bh)
    x1 = min(1.0, bx + bw + pad * bw)
    y1 = min(1.0, by + bh + pad * bh)
    out = [[round(x0, 3), round(y0, 3), round(x1 - x0, 3), round(y1 - y0, 3)]]
    n = int(n or 1)
    if n <= 1:
        return out
    # ---- 其余 n-1 个：产品框内部的能量子区 ----
    grid = 5
    try:
        cells = _energy_grid(src, grid=grid)
    except Exception:                                    # noqa: BLE001
        return out
    sw = max(0.08, min(0.46, bw * 0.62))
    sh = max(0.08, min(0.46, bh * 0.62))
    picked: List[List[float]] = []
    # 每行（grid 行）取"中心落在产品框内"的能量最大格 → 供下面按带微调
    ebest: Dict[int, Tuple[float, int]] = {}
    for cy in range(grid):
        row = [(cells[cy][cx], cx) for cx in range(grid)
               if bx - 0.01 <= (cx + 0.5) / grid <= bx + bw + 0.01
               and by - 0.01 <= (cy + 0.5) / grid <= by + bh + 0.01]
        if row:
            ebest[cy] = max(row)
    # ---- 其余 n-1 个：沿产品高度**等分带**打底，带内有高能量格就微调过去 ----
    # 为什么必须用等分带打底：产品框一偏小，5×5 网格里"中心落在框内"的格就不够
    # n-1 个。而等分带**永远凑得齐**，且天然分散（上部/中部/下部 → 工艺/标识/结构），
    # 绝不会出现"②③ 两张几乎一样"。也**绝不**回退到方案默认坐标——那正是
    # "细节图拍到框外的手"的根因（v0.31.30 真机事故）。
    k = max(0, n - 1)
    for i in range(k):
        bc = by + bh * (i + 0.5) / k          # 本带中心（纵向）
        bcx = bx + bw / 2                     # 默认产品水平中心
        half = bh / k / 2 + 1e-6
        for cy, (_e, cx) in sorted(ebest.items(), key=lambda kv: -kv[1][0]):
            if abs((cy + 0.5) / grid - bc) <= half:
                bcx = (cx + 0.5) / grid       # 带内细节更值得看 → 挪过去
                break
        sx = min(max(bx, bcx - sw / 2), 1.0 - sw)
        sy = min(max(by, bc - sh / 2), 1.0 - sh)
        picked.append([round(sx, 3), round(sy, 3), round(sw, 3), round(sh, 3)])
    return out + picked


# --------------------------------------------------------------------------
# 绘制原语
# --------------------------------------------------------------------------
def _draw_text(p, rect, text, font, color, flags=None, line_gap=0):
    """在 rect 内写字（默认左对齐 + 自动换行）。返回实际用掉的高度。"""
    q = _qt()
    p.setFont(font)
    p.setPen(_c(color))
    fl = flags if flags is not None else (q["Qt"].AlignmentFlag.AlignLeft |
                                          q["Qt"].AlignmentFlag.AlignTop |
                                          q["Qt"].TextFlag.TextWordWrap)
    if line_gap:
        fm = q["QFontMetrics"](font)
        y = rect.y() if hasattr(rect, "y") else rect.top()
        for ln in _wrap(fm, rect.width(), str(text or "")):
            p.drawText(rect.left(), y + fm.ascent(), ln)
            y += fm.height() + line_gap
        return int(y)
    p.drawText(rect, fl, str(text or ""))
    return rect.height() if hasattr(rect, "height") else 0


def _wrap(fm, width: int, text: str) -> List[str]:
    """按像素宽度硬折行（中文逐字、英文按词）。"""
    out, line = [], ""
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\n":
            out.append(line)
            line = ""
            i += 1
            continue
        nxt = line + ch
        if fm.horizontalAdvance(nxt) > width and line:
            out.append(line)
            line = ch
        else:
            line = nxt
        i += 1
    if line:
        out.append(line)
    return out or [""]


def _fit_rect(img, max_w: int, max_h: int, cx: int, cy: int):
    """把图等比缩进 max_w×max_h，返回 (QRect 目标, QRect 源, 缩放比)。"""
    q = _qt()
    iw, ih = img.width(), img.height()
    if iw <= 0 or ih <= 0:
        return None, None, 0.0
    k = min(max_w / iw, max_h / ih)
    w, h = max(1, int(iw * k)), max(1, int(ih * k))
    tgt = q["QRect"](int(cx - w / 2), int(cy - h / 2), w, h)
    src = q["QRect"](0, 0, iw, ih)
    return tgt, src, k


def _soft_shadow(p, rect, radius=14, spread=10):
    """很轻的落地阴影（画 3 层半透明圆角矩形，成本低、观感够）。"""
    q = _qt()
    p.setPen(q["Qt"].PenStyle.NoPen)
    for i in range(3):
        a = 26 - i * 8
        p.setBrush(_c("rgba(15,23,42,%d)" % a))
        r = q["QRectF"](rect.x() - spread + i * 3, rect.y() + spread - i * 2,
                        rect.width() + (spread - i * 3) * 2, rect.height() + 6)
        p.drawRoundedRect(r, radius, radius)


def crop_zoom(src, box, size=(800, 800), zoom: float = 2.0):
    """按**相对坐标** box 取样 → 推近 zoom 倍 → 缩放成目标尺寸（**不变形**）。

    做法：先按目标宽高比把源窗收紧成"同比例"，再从中心内收 1/zoom（模拟镜头推近），
    最后一次 `scaled`。若直接 `scaled(ow, oh)` 而源窗比例不同，产品会被拉扁/拉长。
    """
    q = _qt()
    if isinstance(size, (tuple, list)):
        ow = int(size[0])
        oh = int(size[1]) if len(size) > 1 else ow
    else:
        ow = oh = int(size)
    ow, oh = max(8, ow), max(8, oh)
    iw, ih = max(2, src.width()), max(2, src.height())
    bx, by = float(box[0]) * iw, float(box[1]) * ih
    bw = max(2.0, float(box[2]) * iw)
    bh = max(2.0, float(box[3]) * ih)
    ar = ow / float(oh)
    if bw / bh > ar:
        cw, ch = bh * ar, bh
    else:
        cw, ch = bw, bw / ar
    z = max(1.0, float(zoom))
    cw, ch = cw / z, ch / z
    cx, cy = bx + bw / 2.0, by + bh / 2.0
    x = int(max(0, min(cx - cw / 2.0, iw - cw)))
    y = int(max(0, min(cy - ch / 2.0, ih - ch)))
    w = int(max(2, min(cw, iw - x)))
    h = int(max(2, min(ch, ih - y)))
    sub = src.copy(q["QRect"](x, y, w, h))
    return sub.scaled(ow, oh, q["Qt"].AspectRatioMode.IgnoreAspectRatio,
                      q["Qt"].TransformationMode.SmoothTransformation)


# --------------------------------------------------------------------------
# 主图 5 张
# --------------------------------------------------------------------------
def render_main_image(plan: dict, item: dict, src=None, matte=None,
                      matte_info=None) -> "QImage":
    """画一张 800×800 主图。src/matte 为空时画"待生成"占位（**不画假图**）。"""
    q = _qt()
    spec = plan.get("spec") or {}
    w, h = tuple(spec.get("main_size") or (800, 800))
    lay = item.get("layout") or "plain"
    brand = str(plan.get("brand") or "")
    img = blank(w, h, "#ffffff")
    p = q["QPainter"](img)
    try:
        p.setRenderHint(q["QPainter"].RenderHint.Antialiasing, True)
        p.setRenderHint(q["QPainter"].RenderHint.SmoothPixmapTransform, True)
        p.setRenderHint(q["QPainter"].RenderHint.TextAntialiasing, True)
        ink = "#0f172a"
        accent = "#0ea5e9"
        base = matte if matte is not None else src

        if lay == "plain":
            if base is not None:
                tgt, srect, _k = _fit_rect(base, int(w * _PRODUCT_RATIO),
                                           int(h * _PRODUCT_RATIO), w // 2, h // 2)
                if tgt is not None:
                    _mi = matte_info or {}
                    # 抠图成功且投影已烘进 alpha → 不再叠影子（双层影=脏）
                    if not (_mi.get("applied") and _mi.get("shadow_kept")):
                        _soft_shadow(p, q["QRectF"](tgt.x() + 6, tgt.y() + 10,
                                                    tgt.width() - 12, tgt.height() - 6))
                    p.drawImage(tgt, base, srect)
                    # 浅色产品在纯白底上会"消失" —— 专业白底图会加一圈极细描边
                    if _mi.get("applied") and float(_mi.get("fg_luma") or 0) > 232:
                        p.setPen(q["QPen"](_c("#e6e8ec"), 2))
                        p.setBrush(q["Qt"].BrushStyle.NoBrush)
                        p.drawRect(tgt.adjusted(0, 0, -1, -1))
            else:
                _placeholder(p, w, h, "主图（白底）")
        elif lay == "scene":
            grad = q["QLinearGradient"](0, 0, w, h)
            grad.setColorAt(0.0, _c("#eaf4ff"))
            grad.setColorAt(1.0, _c("#ffffff"))
            p.fillRect(q["QRect"](0, 0, w, h), q["QBrush"](grad))
            if base is not None:
                tgt, srect, _k = _fit_rect(base, int(w * 0.74), int(h * 0.72),
                                           w // 2, int(h * 0.45))
                if tgt is not None:
                    _soft_shadow(p, q["QRectF"](tgt.x() + 8, tgt.y() + 12,
                                                tgt.width() - 16, tgt.height() - 8))
                    p.drawImage(tgt, base, srect)
            _band(p, w, h, item.get("title", ""), item.get("sub", ""), accent)
        elif lay == "sell":
            p.fillRect(q["QRect"](0, 0, int(w * 0.44), h), _c("#0ea5e9"))
            if base is not None:
                tgt, srect, _k = _fit_rect(base, int(w * 0.60), int(h * 0.80),
                                           int(w * 0.66), h // 2)
                if tgt is not None:
                    p.drawImage(tgt, base, srect)
            _draw_text(p, q["QRect"](36, 130, int(w * 0.38), 240),
                       item.get("title", ""), _font(58, True), "#ffffff",
                       q["Qt"].AlignmentFlag.AlignLeft | q["Qt"].TextFlag.TextWordWrap,
                       line_gap=10)
            _draw_text(p, q["QRect"](36, 430, int(w * 0.38), 320),
                       item.get("sub", ""), _font(23), "#e0f2fe",
                       q["Qt"].AlignmentFlag.AlignLeft | q["Qt"].TextFlag.TextWordWrap)
        elif lay == "detail":
            box = (plan.get("crops") or [{"box": [0.3, 0.3, 0.4, 0.4]}])[0]["box"]
            if src is not None:
                top = crop_zoom(src, box, (w, int(h * 0.78)), float(
                    (plan.get("crops") or [{"zoom": 2.0}])[0].get("zoom", 2.0)))
                p.drawImage(q["QRect"](0, 0, w, int(h * 0.78)),
                            top, q["QRect"](0, 0, top.width(), top.height()))
            else:
                _placeholder(p, w, int(h * 0.78), "细节图")
            _band(p, w, h, item.get("title", ""), item.get("sub", ""), "#f59e0b")
        else:                                    # spec：参数表主图
            p.fillRect(q["QRect"](0, 0, w, 96), _c("#0f172a"))
            _draw_text(p, q["QRect"](28, 22, w - 56, 60), item.get("title", ""),
                       _font(38, True), "#ffffff")
            y = 130
            fm = q["QFontMetrics"](_font(26))
            for row in (plan.get("spec_rows") or [])[:9]:
                p.setPen(_c("#64748b"))
                p.setFont(_font(26))
                p.drawText(q["QRect"](40, y, 220, 40),
                           q["Qt"].AlignmentFlag.AlignLeft, str(row[0]))
                p.setPen(_c(ink))
                p.setFont(_font(26, True))
                p.drawText(q["QRect"](270, y, w - 310, 40),
                           q["Qt"].AlignmentFlag.AlignLeft, str(row[1]))
                p.setPen(q["QPen"](_c("#e2e8f0"), 1))
                p.drawLine(40, y + 46, w - 40, y + 46)
                y += 62
                if y > h - 90:
                    break
            _draw_text(p, q["QRect"](40, h - 76, w - 80, 50),
                       "7 天无理由 · 正品保障 · 48h 发货", _font(24), accent)
        # 品牌角标（有才画）
        if brand:
            p.setPen(_c("#94a3b8"))
            p.setFont(_font(20))
            p.drawText(q["QRect"](20, 18, 300, 30),
                       q["Qt"].AlignmentFlag.AlignLeft, brand)
        p.setPen(_c("#cbd5e1"))
        p.setFont(_font(18))
        p.drawText(q["QRect"](0, h - 30, w - 20, 24),
                   q["Qt"].AlignmentFlag.AlignRight, item.get("role", ""))
    finally:
        p.end()
    return img


def _band(p, w, h, title, sub, accent):
    """底部说明条（标题 + 副标题）。"""
    q = _qt()
    top = int(h * 0.80)
    p.setPen(q["Qt"].PenStyle.NoPen)
    p.setBrush(_c("rgba(15,23,42,215)"))
    p.drawRect(q["QRect"](0, top, w, h - top))
    p.setBrush(_c(accent))
    p.drawRect(q["QRect"](0, top, 12, h - top))
    _draw_text(p, q["QRect"](36, top + 20, w - 72, 58),
               title, _font(38, True), "#ffffff")
    _draw_text(p, q["QRect"](36, top + 84, w - 72, 56),
               sub, _font(21), "#cbd5e1",
               q["Qt"].AlignmentFlag.AlignLeft | q["Qt"].TextFlag.TextWordWrap)


def _placeholder(p, w, h, label):
    """占位块：斜纹底 + 「待生成」。**绝不画假产品**。"""
    q = _qt()
    for i in range(-h, w, 44):
        p.setPen(q["QPen"](_c("#eef2f7"), 12))
        p.drawLine(i, h, i + h, 0)
    p.setPen(_c("#94a3b8"))
    _draw_text(p, q["QRect"](0, h // 2 - 30, w, 40), label,
               _font(34, True), "#94a3b8", q["Qt"].AlignmentFlag.AlignCenter)
    _draw_text(p, q["QRect"](0, h // 2 + 16, w, 30), "待生成（缺少实拍图）",
               _font(20), "#b6c2cf", q["Qt"].AlignmentFlag.AlignCenter)


# --------------------------------------------------------------------------
# 局部图 / 细节图
# --------------------------------------------------------------------------
def render_crop_image(plan: dict, crop: dict, src, size=(800, 800)):
    """从原图裁切放大一张局部/细节图，并在右下角压编号标注。"""
    q = _qt()
    side = int(size[0]) if isinstance(size, (tuple, list)) else int(size)
    img = crop_zoom(src, crop.get("box"), (side, side), float(crop.get("zoom", 2.0)))
    p = q["QPainter"](img)
    try:
        p.setRenderHint(q["QPainter"].RenderHint.Antialiasing, True)
        p.setRenderHint(q["QPainter"].RenderHint.TextAntialiasing, True)
        p.setPen(q["Qt"].PenStyle.NoPen)
        p.setBrush(_c("rgba(15,23,42,190)"))
        p.drawRoundedRect(q["QRectF"](16, 16, 74, 74), 12, 12)
        _draw_text(p, q["QRect"](16, 16, 74, 74), str(crop.get("anno", "")),
                   _font(40, True), "#ffffff", q["Qt"].AlignmentFlag.AlignCenter)
        cap = str(crop.get("caption") or crop.get("name") or "")
        if cap:
            p.setBrush(_c("rgba(15,23,42,200)"))
            p.drawRect(q["QRect"](0, side - 92, side, 92))
            _draw_text(p, q["QRect"](28, side - 92 + 14, side - 56, 80),
                       cap, _font(26, True), "#ffffff",
                       q["Qt"].AlignmentFlag.AlignLeft | q["Qt"].TextFlag.TextWordWrap)
    finally:
        p.end()
    return img


# --------------------------------------------------------------------------
# 详情长图
# --------------------------------------------------------------------------
def render_detail_long(plan: dict, images: dict, out_dir: str) -> str:
    """把方案里的七屏拼成一张 **750/790 宽** 的详情长图（PNG）。

    `images` = {"banner": path, "scene": path, "crops": [path,...]}
    缺图的那一屏画占位块并写「待生成」——**如实告知，不画假图**。
    """
    q = _qt()
    spec = plan.get("spec") or {}
    W = int(spec.get("detail_width") or 750)
    img_map = dict(images or {})
    crops_img = [str(x) for x in (img_map.get("crops") or []) if x]

    def _load(p):
        return load_image(p) if p else None

    banner = _load(img_map.get("banner") or img_map.get("main"))
    scene = _load(img_map.get("scene"))
    crops = [(_load(p), c) for p, c in zip(crops_img + [""] * 8,
                                           plan.get("crops") or [])]

    pad, gap = 26, 18
    inner = W - pad * 2
    # ---- 预排：先算每屏高度，再一次性开画布（避免二次绘制）----
    blocks: List[Tuple[str, int, dict]] = []
    h_title = 92
    blocks.append(("title", h_title, {}))
    h_banner = int(inner * 0.62) + 78 if banner is not None else 300 + 78
    blocks.append(("banner", h_banner, {}))
    blocks.append(("head", 78, {"text": plan.get("detail_sections", [{}])[1].get(
        "title", "你是不是也担心这几个问题？")}))
    blocks.append(("pain", 46 * len((plan.get("detail_sections") or
                                     [{}, {"items": []}])[1].get("items") or []) + 24, {}))
    blocks.append(("head", 78, {"text": "为什么选它"}))
    for _pt in (plan.get("selling_points") or []):
        blocks.append(("point", 132, {"pt": _pt}))
    blocks.append(("head", 78, {"text": "细节放大看"}))
    for i, (im, cr) in enumerate(crops):
        if i >= len(plan.get("crops") or []):
            break
        blocks.append(("crop", (int(inner * 0.86) if im is not None else 300) + 62,
                       {"cr": cr, "im": im}))
    blocks.append(("head", 78, {"text": "使用场景"}))
    blocks.append(("scene", (int(inner * 0.62) if scene is not None else 300) + 40, {}))
    blocks.append(("head", 78, {"text": "产品参数"}))
    rows = list(plan.get("spec_rows") or [])[:14]
    blocks.append(("spec", 56 * len(rows) + 24, {"rows": rows}))
    blocks.append(("head", 78, {"text": "购买保障"}))
    svc = (plan.get("detail_sections") or [{}])[-1].get("items") or []
    blocks.append(("service", 46 * len(svc) + 30, {"items": svc}))

    H = sum(b[1] for b in blocks) + gap * len(blocks) + pad
    img = blank(W, max(400, H), "#ffffff")
    p = q["QPainter"](img)
    notes: List[str] = []
    try:
        p.setRenderHint(q["QPainter"].RenderHint.Antialiasing, True)
        p.setRenderHint(q["QPainter"].RenderHint.SmoothPixmapTransform, True)
        p.setRenderHint(q["QPainter"].RenderHint.TextAntialiasing, True)
        y = pad
        for kind, bh, meta in blocks:
            if kind == "title":
                _draw_text(p, q["QRect"](pad, y + 8, inner, 52),
                           plan.get("title", {}).get("title") or plan.get("product", ""),
                           _font(34, True), "#0f172a",
                           q["Qt"].AlignmentFlag.AlignLeft | q["Qt"].TextFlag.TextWordWrap)
                _draw_text(p, q["QRect"](pad, y + 58, inner, 28),
                           "%s · %s" % (plan.get("platform_label", ""),
                                        plan.get("category", "")),
                           _font(20), "#94a3b8")
            elif kind == "head":
                p.setPen(q["Qt"].PenStyle.NoPen)
                p.setBrush(_c("#0ea5e9"))
                p.drawRect(q["QRect"](pad, y + 20, 6, 34))
                _draw_text(p, q["QRect"](pad + 18, y + 18, inner - 18, 42),
                           meta.get("text", ""), _font(28, True), "#0f172a")
            elif kind == "banner":
                if banner is not None:
                    tgt, srect, _k = _fit_rect(banner, inner, int(inner * 0.62),
                                               W // 2, y + int(inner * 0.31))
                    if tgt is not None:
                        p.drawImage(tgt, banner, srect)
                else:
                    _placeholder(p, W, 300, "主视觉")
                    notes.append("banner 无图")
                _draw_text(p, q["QRect"](pad, y + bh - 62, inner, 30),
                           plan.get("product", ""), _font(26, True), "#0f172a",
                           q["Qt"].AlignmentFlag.AlignCenter)
            elif kind == "pain":
                items = (plan.get("detail_sections") or [{}, {"items": []}])[1].get("items") or []
                yy = y + 8
                for it in items:
                    _draw_text(p, q["QRect"](pad + 10, yy, inner - 10, 40),
                               "✕ " + str(it), _font(23), "#b91c1c")
                    yy += 46
            elif kind == "point":
                pt = meta.get("pt") or {}
                p.setPen(q["Qt"].PenStyle.NoPen)
                p.setBrush(_c("#f8fafc"))
                p.drawRoundedRect(q["QRectF"](pad, y + 6, inner, 118), 10, 10)
                p.setBrush(_c("#0ea5e9"))
                p.drawRect(q["QRect"](pad, y + 6, 5, 118))
                _draw_text(p, q["QRect"](pad + 20, y + 18, inner - 40, 38),
                           str(pt.get("head", "")), _font(26, True), "#0f172a")
                _draw_text(p, q["QRect"](pad + 20, y + 58, inner - 40, 60),
                           str(pt.get("body", "")), _font(21), "#475569")
            elif kind == "crop":
                im, cr = meta.get("im"), meta.get("cr") or {}
                if im is not None:
                    tgt, srect, _k = _fit_rect(im, int(inner * 0.86), int(inner * 0.86),
                                               W // 2, y + int(inner * 0.43))
                    if tgt is not None:
                        p.drawImage(tgt, im, srect)
                else:
                    _placeholder(p, W, 300, str(cr.get("name", "细节图")))
                    notes.append("细节图 %s 无图" % cr.get("anno", ""))
                _draw_text(p, q["QRect"](pad, y + bh - 50, inner, 40),
                           "%s %s" % (cr.get("anno", ""), cr.get("caption", "")),
                           _font(21), "#475569",
                           q["Qt"].AlignmentFlag.AlignCenter)
            elif kind == "scene":
                if scene is not None:
                    tgt, srect, _k = _fit_rect(scene, inner, int(inner * 0.62),
                                               W // 2, y + int(inner * 0.31))
                    if tgt is not None:
                        p.drawImage(tgt, scene, srect)
                else:
                    _placeholder(p, W, 300, "场景图")
                    notes.append("场景图无图")
            elif kind == "spec":
                yy = y + 10
                fm_lbl = _font(22)
                for r in meta.get("rows") or []:
                    p.setPen(_c("#f1f5f9"))
                    p.setBrush(_c("#f8fafc"))
                    p.drawRect(q["QRect"](pad, yy, inner, 52))
                    _draw_text(p, q["QRect"](pad + 16, yy + 13, 200, 34),
                               str(r[0]), fm_lbl, "#64748b")
                    _draw_text(p, q["QRect"](pad + 230, yy + 12, inner - 246, 36),
                               str(r[1]), _font(22, True), "#0f172a")
                    yy += 56
            elif kind == "service":
                yy = y + 10
                for it in meta.get("items") or []:
                    _draw_text(p, q["QRect"](pad + 10, yy, inner - 10, 40),
                               "✓ " + str(it), _font(22), "#166534")
                    yy += 46
            y += bh + gap
        # 页脚（诚实标注来源）
        _draw_text(p, q["QRect"](pad, y, inner, 30),
                   "本图由 PASM Studio 电商套件合成 · 产品画面取自实拍图，未经 AI 重绘",
                   _font(18), "#94a3b8", q["Qt"].AlignmentFlag.AlignCenter)
    finally:
        p.end()
    out = os.path.join(out_dir, "详情长图.png")
    ok = save_image(img, out)
    render_detail_long.last_notes = notes
    render_detail_long.last_size = (W, max(400, H))
    # ★ v0.31.28：把「逐块高度」记下来 —— `render_detail_slices()` 靠它在**块边界**
    #   裁切，这样切出来的每张详情图与长图**逐像素一致**（比"再画一遍"稳得多：
    #   重画要么养两套绘制代码迟早漂移，要么把长图函数拆两半、风险高）。
    render_detail_long.last_blocks = [(k, bh) for k, bh, _ in blocks]
    render_detail_long.last_pad = pad
    render_detail_long.last_gap = gap
    return out if ok else ""


#: 自然分屏：每个「小标题块」开一屏（`head` 就是各屏的分界）
def _natural_groups(blocks):
    groups, cur = [], []
    for i, (k, _h) in enumerate(blocks):
        if k == "head" and cur:
            groups.append(cur)
            cur = []
        cur.append(i)
    if cur:
        groups.append(cur)
    return groups


def _split_by_count(blocks, count: int):
    """把**连续的块**分成正好 `count` 组（块不可切，免得切坏文字）。

    ★ 做法：在**块边界**里挑「最接近等分点」的切点（不是「累加到均值就收口」）。
      后者会因块高很不均匀而分不够（实测：要 10 张只给出 6 张 —— banner 与裁切块
      动辄 550~660px，占了大半高度，贪心收口到后面就没块可分了）。
      挑切点则**张数一定准**（`count ≤ 块数` 时），代价是各张高度不一定完全相等 ——
      对「一屏一张」的详情图来说，**张数准确比高度均匀重要得多**。
    """
    n = len(blocks)
    count = max(1, min(int(count), n))
    if count <= 1:
        return [list(range(n))]
    heights = [h for _k, h in blocks]
    cum = [0]
    for h in heights:
        cum.append(cum[-1] + int(h))
    total = cum[-1] or 1
    cuts: List[int] = []
    for i in range(1, count):
        want = total * i / float(count)
        lo = (cuts[-1] if cuts else 0) + 1
        hi = n - (count - i)                     # 后面每屏至少留一块
        if lo > hi:
            break
        best = lo
        for j in range(lo, hi + 1):
            if abs(cum[j] - want) < abs(cum[best] - want):
                best = j
        cuts.append(best)
    groups, prev = [], 0
    for c in cuts + [n]:
        groups.append(list(range(prev, c)))
        prev = c
    return groups


def render_detail_slices(plan: dict, images: dict, out_dir: str,
                         long_path: str = "",
                         count: Optional[int] = None) -> List[str]:
    """把详情长图**按屏**切成若干张独立详情图（宽度同版心），返回文件路径列表。

    为什么要切：淘宝/京东的详情区**可以多图上传**，商家习惯一屏一张；而整张
    5000+ px 的长图在个别编辑器里会被压缩得看不清。**两种都给**最稳。

    ★ 实现取"**裁长图**"而不是"重画一遍"：在**块边界**裁，切出来的每张与长图
      逐像素一致，且只有一套绘制代码（不会出现"长图改了、切片没跟上"的漂移）。

    `count` 为空 → **一屏一张**（按小标题自然分屏）；给了数字 → 尽量均分成那么多张。
    返回 `[]` 表示没切成（缺长图/缺布局信息），此时调用方保留长图即可。
    """
    q = _qt()
    src = str(long_path or "")
    if not (src and os.path.isfile(src)):
        return []
    blocks = getattr(render_detail_long, "last_blocks", None)
    pad = int(getattr(render_detail_long, "last_pad", 26) or 26)
    gap = int(getattr(render_detail_long, "last_gap", 18) or 18)
    if not blocks:
        return []
    img = load_image(src)
    if img is None or img.isNull():
        return []
    W, H = img.width(), img.height()
    ys, y = [], pad
    for _k, h in blocks:
        ys.append(y)
        y += int(h) + gap
    groups = (_split_by_count(blocks, count) if count else _natural_groups(blocks))
    out_files: List[str] = []
    for idx, grp in enumerate(groups, 1):
        if not grp:
            continue
        y0 = ys[grp[0]]
        if grp[-1] + 1 < len(blocks):
            y1 = ys[grp[-1] + 1] - gap          # 去掉与下一屏之间的间隙
        else:
            y1 = H                              # 最后一屏带到页脚
        y0 = max(0, min(int(y0), H - 8))
        y1 = max(y0 + 40, min(int(y1), H))
        sub = img.copy(q["QRect"](0, y0, W, y1 - y0))
        if sub is None or sub.isNull():
            continue
        fp = os.path.join(out_dir, "详情图%02d.png" % idx)
        if save_image(sub, fp):
            out_files.append(fp)
    return out_files


# --------------------------------------------------------------------------
# 总入口
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# 总入口
# --------------------------------------------------------------------------
def _auto_product_box(src_path: str):
    """懒加载 percept，用云端视觉问出产品框；任何一步失败都返回 None（绝不炸）。

    为什么要"自动"：调用方（executors / pasm_companion）不该被迫知道视觉配置的
    细节——它们只要把实拍图路径传进来，定位由这里自己完成。

    ★ 0.31.30：配置按**候选列表**找。源码运行时 `logsetup.data_dir()` 指向
    `<repo>/.pasmstudio_dev`（里面没有 Key），只认它一条会让"自动定位"在源码
    环境下永远失效（真机却是好的）——所以再兜一个 `%APPDATA%/PASMStudio`，
    用户真实配置在哪都能读到。纯只读，绝不写任何文件。
    """
    if not src_path or not os.path.isfile(src_path):
        return None
    try:
        from pasm.cognitive import percept as _P
        import json as _json
        try:
            import logsetup as _LS
            dirs = [_LS.data_dir()]
        except Exception:                                # noqa: BLE001
            dirs = []
        if os.environ.get("PASMSTUDIO_DATA"):
            dirs.append(os.environ["PASMSTUDIO_DATA"])
        # 真实用户配置（源码运行时 data_dir 是仓库内的 dev 目录，没有 Key）
        dirs.append(os.path.join(os.environ.get("APPDATA") or
                                 os.path.expanduser("~"), "PASMStudio"))
        cfg = {}
        for d in dirs:
            if not d:
                continue
            cp = os.path.join(d, "config.json")
            if os.path.isfile(cp):
                try:
                    with open(cp, "r", encoding="utf-8") as f:
                        cfg = _json.load(f) or {}
                except Exception:                        # noqa: BLE001
                    continue
            if cfg:
                cloud = _P.resolve_vision_cloud(cfg)
                if cloud:
                    return _P.product_box(src_path, cloud=cloud)
        return None
    except Exception:                                    # noqa: BLE001
        return None


def render_all(plan: dict, src_path: str, out_dir: str,
               images: Optional[dict] = None,
               allow_video_download: bool = False,
               detail_count: Optional[int] = None,
               product_box: Optional[list] = None) -> dict:
    """按方案产出整套图。

    返回 {ok, files:{main:[], crops:[], detail_long:""}, notes:[], reason:""}
    `ok=False` 的**唯一**原因是"没有可用的实拍图"—— 主图不能凭空捏，
    这时候只交出方案（md/HTML），图一张都不生成。

    `product_box`: 归一化 [x,y,w,h] 产品框（percept.product_box 得来）。
    传 None 时自动尝试云端视觉定位；定位不到才回退到高频能量启发式。
    """
    os.makedirs(out_dir, exist_ok=True)
    notes: List[str] = []
    files = {"main": [], "crops": [], "detail_long": "", "detail_slices": []}
    if not available():
        return {"ok": False, "files": files, "notes": notes,
                "reason": "渲染环境不可用（PySide6 未就绪）"}
    _qapp()
    src = load_image(src_path) if src_path and os.path.isfile(src_path) else None
    if src is None:
        return {"ok": False, "files": files, "notes": notes,
                "reason": "没有可用的产品实拍图（喂一张图给我，我就用你的图真合成）"}
    # ---- 局部/细节图裁切区：**产品框优先**（v0.31.30）----
    # 真机事故（2026-10-14）：手托项链的实拍图，细节图裁出来全是手指皮肤
    #  —— "高频能量"选中了皮肤纹理和「2克拉」文字水印，产品一次都没入选。
    if product_box is None:
        product_box = _auto_product_box(src_path)
    boxes: List[List[float]] = []
    if product_box:
        boxes = pick_product_boxes(src, n=len(plan.get("crops") or []),
                                   box=product_box)
    if boxes:
        for c, b in zip(plan.get("crops") or [], boxes):
            c["box"] = b
        notes.append("局部/细节图按**产品实际位置**裁切（视觉模型定位，%d 处）" % len(boxes))
    else:
        boxes = pick_detail_boxes(src, n=len(plan.get("crops") or []))
        if boxes:
            for c, b in zip(plan.get("crops") or [], boxes):
                c["box"] = b
            notes.append("细节/局部图的裁切区域由图像高频能量自动挑选（%d 处）%s"
                         % (len(boxes),
                            "；**未能定位产品位置**（无云端视觉或未答出坐标），"
                            "裁切区可能拍到的不是产品主体" if not product_box else ""))
        else:
            notes.append("未能定位产品位置，细节图沿用方案的默认裁切区域")
    # ---- 去底（专业级；仅白底主图用）----
    # 两条路：① pro_matte（四边多背景色建模）② pro_matte_product（产品框+肤色）
    # 手持实拍①会判「背景过于复杂」整体拒绝 → 交给②；②也有质量闸门，不可靠时
    # 同样老实拒绝（绝不把碎块贴白底当白底主图交出去）。
    matte, minfo = pro_matte(src, keep_shadow=True)
    applied = bool(minfo.get("applied"))
    if not applied and product_box:
        matte, minfo = pro_matte_product(src, box=product_box,
                                         keep_shadow=True)
        applied = bool(minfo.get("applied"))
    if applied:
        _bits = []
        if minfo.get("mode") == "pro":
            _bits.append("多背景色建模")
            _bits.append("去色溢出(除白边)")
        elif minfo.get("mode") == "product":
            _bits.append("按产品框去底(手持实拍)")
            _bits.append("去色溢出(除白边)")
        else:
            _bits.append("简易法(四边同色)")
        if minfo.get("shadow_kept"):
            _bits.append("保留投影")
        notes.append("白底主图已去底：%s" % "、".join(_bits))
    else:
        _why = minfo.get("reason") or "背景过于复杂"
        if product_box and "不可靠" not in _why:
            _why += "（手持/复杂底的自动去底判据不成立）"
        notes.append("未去底（%s）→ 主图改用留白版式，绝不硬抠出白洞" % _why)
    size = tuple(plan.get("spec", {}).get("main_size") or (800, 800))
    # ---- 主图 ----
    for item in (plan.get("main_images") or []):
        im = render_main_image(plan, item, src=src,
                               matte=matte if applied else None,
                               matte_info=minfo)
        fp = os.path.join(out_dir, "主图%d_%s.png" % (item.get("idx", 0),
                                                      item.get("role", "")))
        if save_image(im, fp):
            files["main"].append(fp)
    # ---- 局部图 / 细节图 ----
    for c in (plan.get("crops") or []):
        im = render_crop_image(plan, c, src, size)
        # ⚠️ 名字里的 "/" 会**悄悄变成建子目录**（实测：细节图-工艺/标识 → 建目录
        #    `细节图_工艺/` 再在里面放 `标识.png`，产物数量对不上还不报错）。
        _nm = re.sub(r'[\\/:*?"<>|\s]+', "_",
                     str(c.get("name") or "细节图").split("-")[-1]).strip("_")[:16] or "细节"
        fp = os.path.join(out_dir, "%s_%s.png" % (c.get("kind", "局部图"), _nm))
        if save_image(im, fp):
            files["crops"].append(fp)
    # ---- 详情长图 ----
    imgs = dict(images or {})
    imgs.setdefault("banner", files["main"][0] if files["main"] else "")
    imgs["crops"] = files["crops"]
    files["detail_long"] = render_detail_long(plan, imgs, out_dir)
    if render_detail_long.last_notes:
        notes.append("详情长图有占位屏（如实标注「待生成」）：" +
                     "、".join(render_detail_long.last_notes))
    # ★ v0.31.28：再切一套「按屏的独立详情图」（详情区可多图上传；长图也保留）
    try:
        files["detail_slices"] = render_detail_slices(
            plan, imgs, out_dir, long_path=files["detail_long"],
            count=detail_count)
        if files["detail_slices"]:
            notes.append("详情图已按屏切成 %d 张（长图同时保留，两种都能直接上传）"
                         % len(files["detail_slices"]))
    except Exception as ex:                                  # noqa: BLE001
        notes.append("详情图切分失败（长图不受影响）：%s" % ex)
    # ---- 主图视频（1:1）：有 ffmpeg 出真 mp4；没有就出零依赖 HTML 预览 ----
    files.setdefault("video", "")
    files.setdefault("video_preview", "")
    try:
        import ecom_video as EV
        _vsrc = {"main": files["main"], "crops": files["crops"],
                 "scene": str(imgs.get("scene") or "")}
        _v = EV.render_shop_video(plan, _vsrc, out_dir,
                                  allow_download=allow_video_download)
        if _v.get("ok"):
            files["video"] = _v.get("path") or ""
            notes.append("主图视频：%s" % (_v.get("note") or ""))
        else:
            notes.append("主图视频未出成片（%s）→ 已改出 HTML 预览播放器"
                         % (_v.get("note") or "未知原因"))
            files["video_preview"] = EV.render_video_preview_html(
                plan, _vsrc, os.path.join(out_dir, "主图视频_预览.html"))
    except Exception as ex:                                  # noqa: BLE001
        notes.append("主图视频这一步失败：%s" % ex)
    return {"ok": True, "files": files, "notes": notes, "reason": ""}


# --------------------------------------------------------------------------
# 自检（离屏真渲染：像素级断言，不是"函数返回了字符串"）
# --------------------------------------------------------------------------
def selftest() -> int:
    import shutil
    import tempfile
    passed = failed = 0

    def check(cond, name, detail=""):
        nonlocal passed, failed
        if isinstance(cond, str):
            raise AssertionError("check() 参数顺序写反：应为 check(条件, 名称, 详情)，收到 %r" % cond)
        if cond:
            passed += 1
            print("  ok   %s" % name)
        else:
            failed += 1
            print("  FAIL %s  %s" % (name, detail))

    print("== ecom_render 自检 ==")
    if not available():
        print("  ⚠ PySide6 不可用 → 渲染层整体跳过（调用方会降级 HTML）")
        return 0
    _qapp()
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import ecom as EC

    tmp = tempfile.mkdtemp(prefix="ecomr_")
    try:
        # ---------- 造一张"产品图"：灰底 + 中央彩色方块 + 右上角纹理 ----------
        q = _qt()
        src = blank(900, 900, "#f2f2f2")
        p = q["QPainter"](src)
        p.setPen(q["Qt"].PenStyle.NoPen)
        p.setBrush(_c("#2f6fed"))
        p.drawRoundedRect(q["QRectF"](230, 300, 440, 380), 30, 30)
        p.setBrush(_c("#ffd166"))
        p.drawEllipse(q["QRectF"](300, 360, 180, 180))
        p.setBrush(_c("#111827"))
        for i in range(0, 200, 8):               # 右上角高频纹理
            p.drawRect(q["QRect"](640 + i // 2, 60 + i, 6, 26))
        p.end()
        src_path = os.path.join(tmp, "实拍.png")
        check(save_image(src, src_path), "落盘测试图", src_path)
        check(os.path.getsize(src_path) > 0, "测试图非空")

        # ---------- 读图 ----------
        im = load_image(src_path)
        check(im is not None and im.width() == 900, "读图成功且尺寸正确",
              None if im is None else (im.width(), im.height()))
        check(load_image(os.path.join(tmp, "不存在.png")) is None,
              "不存在的图返回 None（不抛）")

        # ---------- 抠图：正例（纯灰底应能抠掉）----------
        matte, applied = auto_matte(im)
        check(applied, "★ 纯色底能抠图（applied=True）")
        if applied:
            check(matte.pixelColor(5, 5).alpha() == 0, "★ 四角被抠成透明",
                  matte.pixelColor(5, 5).alpha())
            check(matte.pixelColor(450, 500).alpha() == 255,
                  "★ 产品内部**没有**被误抠", matte.pixelColor(450, 500).alpha())
        # ---------- 抠图：反例（花色底不该硬抠）----------
        busy = blank(600, 600, "#ffffff")
        p = q["QPainter"](busy)
        for i in range(0, 600, 30):
            p.setPen(q["QPen"](_c("#" + ("%06x" % ((i * 7919) % 0xffffff))), 8))
            p.drawLine(0, i, 600, i)
        p.end()
        _m2, applied2 = auto_matte(busy)
        check(not applied2, "★ 反例：非纯色背景**不许**硬抠（applied=False）")

        # ---------- 自动挑细节区域 ----------
        boxes = pick_detail_boxes(im, n=3)
        check(len(boxes) == 3, "挑出 3 个细节区域", boxes)
        check(all(len(b) == 4 and 0 <= b[0] <= 1 - b[2] + 1e-6 for b in boxes),
              "细节框是相对坐标且在界内", boxes)
        hit_right_top = any(b[0] >= 0.6 and b[1] <= 0.3 for b in boxes)
        check(hit_right_top, "★ 右下/右上高频纹理区被挑中（能量判据真的在起作用）", boxes)
        uniform = blank(300, 300, "#dddddd")
        check(pick_detail_boxes(uniform, n=3) == [],
              "★ 反例：纯色图挑不出细节区（返回空，交给兜底）")

        # ---------- v0.31.30：按**产品框**裁切（修"细节图拍到手"）----------
        # 本图的布局：蓝矩形=产品（0.25~0.74, 0.33~0.76），右上角那堆高频纹理=杂讯
        #   （对应真机上的手指皮肤与「2克拉」文字水印）。
        pb = [0.25, 0.33, 0.49, 0.42]
        pboxes = pick_product_boxes(im, n=3, box=pb)
        check(len(pboxes) == 3, "按产品框推出 3 个裁切区", pboxes)
        check(all(len(b) == 4 and 0 <= b[0] and b[0] + b[2] <= 1.0001
                  and 0 <= b[1] and b[1] + b[3] <= 1.0001 for b in pboxes),
              "产品裁切区是相对坐标且不出界", pboxes)
        check(abs(pboxes[0][0] - (0.25 - 0.14 * 0.49)) < 0.02
              and abs(pboxes[0][1] - (0.33 - 0.14 * 0.42)) < 0.02,
              "第 1 个裁切区就是产品主体（外扩 14% 收边工艺）", pboxes[0])
        # ★ 核心断言：**不许**再选中右上角那片高频杂讯
        #   —— 旧判据正是在这里失手，把细节图裁成了手指。
        check(not any(b[0] >= 0.6 and b[1] <= 0.3 for b in pboxes),
              "★ 产品框内的裁切区不会跑到框外的高频杂讯上（修「对着手」）", pboxes)
        check(all(pb[0] - 0.02 <= b[0] + b[2] / 2 <= pb[0] + pb[2] + 0.02 and
                  pb[1] - 0.02 <= b[1] + b[3] / 2 <= pb[1] + pb[3] + 0.02
                  for b in pboxes),
              "所有裁切区中心都落在产品框内", pboxes)
        # 三个区必须**真的散开**（曾出现②③几乎重合 = 白送两张一样的图）
        _cs = [(b[0] + b[2] / 2, b[1] + b[3] / 2) for b in pboxes]
        check(all(abs(_cs[i][1] - _cs[j][1]) > 0.01 or
                  abs(_cs[i][0] - _cs[j][0]) > 0.01
                  for i in range(len(_cs)) for j in range(i + 1, len(_cs))),
              "三个裁切区互不重合（不是复制同一张）", _cs)
        # 产品框偏小（真机上视觉模型常给紧框）→ 仍要凑满 3 个、仍在框内、仍散开
        tight = pick_product_boxes(im, n=3, box=[0.4, 0.45, 0.12, 0.10])
        check(len(tight) == 3, "产品框偏小时仍凑满 3 个裁切区", tight)
        check(all(0.4 - 0.02 <= b[0] + b[2] / 2 <= 0.4 + 0.12 + 0.02 and
                  0.45 - 0.02 <= b[1] + b[3] / 2 <= 0.45 + 0.10 + 0.02
                  for b in tight),
              "产品框偏小时裁切区中心仍在框内（不会溜到框外拍手）", tight)
        _tc = [(b[1] + b[3] / 2) for b in tight]
        check(len({round(v, 3) for v in _tc}) == 3,
              "产品框偏小时三个裁切区仍纵向散开", _tc)
        check(pick_product_boxes(im, n=3, box=None) == [],
              "★ 反例：拿不到产品框时返回空（交给高频能量兜底）")
        check(pick_product_boxes(im, n=3, box=[0.5, 0.5, 0.001, 0.9]) == [],
              "★ 反例：退化的产品框返回空")
        check(pick_product_boxes(im, n=3, box="垃圾") == [],
              "★ 反例：非法产品框返回空")

        # ---------- 裁切放大：真的放大了 ----------
        crop = crop_zoom(im, [0.3, 0.3, 0.3, 0.3], (400, 400), 2.0)
        check(crop.width() == 400 and crop.height() == 400, "裁切输出尺寸正确",
              (crop.width(), crop.height()))
        c_small = crop_zoom(im, [0.3, 0.3, 0.3, 0.3], (400, 400), 1.0)
        # 放大倍数越大 → 源窗越小 → 画面细节越少但更近；用"与中心点等于产品色"验证确实取到了主体
        check(crop.pixelColor(200, 200).alpha() == 255, "裁切画面非空")

        # ---------- 方案 + 全量渲染 ----------
        plan = EC.build_plan("加厚保暖外套，防水面料，易清洗，品牌：示范，价格：199",
                             vision="一件深灰色连帽外套", platform="taobao")
        out_dir = os.path.join(tmp, "out")
        # product_box=[] → 显式关掉"自动问云端视觉定位产品"，自检不依赖网络
        r = render_all(plan, src_path, out_dir, product_box=[])
        check(r["ok"], "render_all 成功", r.get("reason"))
        check(len(r["files"]["main"]) == 5, "主图 5 张落盘",
              r["files"]["main"])
        check(len(r["files"]["crops"]) == 3, "局部/细节图 3 张落盘",
              r["files"]["crops"])
        check(bool(r["files"]["detail_long"]) and
              os.path.getsize(r["files"]["detail_long"]) > 1000,
              "详情长图落盘且非空", r["files"]["detail_long"])
        for f in r["files"]["main"]:
            _i = load_image(f)
            check(_i is not None and _i.width() == 800 and _i.height() == 800,
                  "主图尺寸 800×800：%s" % os.path.basename(f),
                  None if _i is None else (_i.width(), _i.height()))
        # 白底主图必须真是白底（四角白）—— "第 1 张白底"不是靠说的
        m0 = load_image(r["files"]["main"][0])
        check(m0 is not None and m0.pixelColor(3, 3).red() > 240
              and m0.pixelColor(3, 3).green() > 240,
              "★ 主图第 1 张四角是白的（真白底，不是口号）",
              None if m0 is None else m0.pixelColor(3, 3).name())
        # 主图不能是"全白空图"—— 中心必须有内容
        check(m0 is not None and m0.pixelColor(400, 400).alpha() == 255
              and m0.pixelColor(400, 400) != _c("#ffffff"),
              "★ 主图中心有真实画面（不是一张空白 PNG）",
              None if m0 is None else m0.pixelColor(400, 400).name())
        # 局部图/细节图必须与主图**不是同一张**（证明真的裁了不同区域）
        c1 = load_image(r["files"]["crops"][0])
        c2 = load_image(r["files"]["crops"][-1])
        check(c1 is not None and c2 is not None
              and bytes(c1.constBits()) != bytes(c2.constBits()),
              "★ 两张细节图内容不同（证明裁的是不同区域，不是复制同一张）")
        dl = load_image(r["files"]["detail_long"])
        check(dl is not None and dl.width() == 750, "详情长图版心 750（淘宝）",
              None if dl is None else dl.width())
        check(dl is not None and dl.height() > 1500,
              "详情长图确实是「长图」（高度 > 1500）",
              None if dl is None else dl.height())
        # ---------- v0.31.28：按屏切分的独立详情图 ----------
        _sl = r["files"].get("detail_slices") or []
        check(len(_sl) >= 3,
              "★ render_all 真产出了按屏切分的独立详情图", len(_sl))
        check(all(os.path.isfile(x) and os.path.getsize(x) > 1000 for x in _sl),
              "★ 每张独立详情图都真在盘上且非空", _sl)
        _sw = [load_image(x).width() for x in _sl]
        check(all(w == 750 for w in _sw),
              "★ 每张详情图宽度 = 平台版心（淘宝 750）", _sw)
        _tot = sum(load_image(x).height() for x in _sl)
        # ⚠️ 相邻两屏之间那条**空白间隙**本来就不带进切片（少掉的是空白，不是内容）——
        #    所以容差要 = 间隙×屏数，不能拍一个固定小数（第一版就假红在这上面）。
        _gap = int(getattr(render_detail_long, "last_gap", 18) or 18)
        _lh = dl.height() if dl is not None else 0
        check(_tot >= _lh - _gap * (len(_sl) + 1) - 40,
              "★ 切片之和 ≈ 长图高度（只少掉屏间空白，没有丢内容）",
              (_tot, _lh, _gap, len(_sl)))
        _sl10 = render_detail_slices(plan, {}, out_dir,
                                     long_path=r["files"]["detail_long"], count=10)
        check(len(_sl10) == 10, "★ 指定「10 张」时真切成 10 张", len(_sl10))
        _sl6 = render_detail_slices(plan, {}, out_dir,
                                    long_path=r["files"]["detail_long"], count=6)
        check(len(_sl6) == 6, "★ 指定 6 张也给 6 张（不是只有 10 凑巧过）", len(_sl6))
        _sl1 = render_detail_slices(plan, {}, out_dir,
                                    long_path=r["files"]["detail_long"], count=1)
        check(len(_sl1) == 1, "指定 1 张时退化为整张（等价长图）", len(_sl1))
        _nblk = len(getattr(render_detail_long, "last_blocks", []) or [])
        _sl99 = render_detail_slices(plan, {}, out_dir,
                                     long_path=r["files"]["detail_long"], count=99)
        check(len(_sl99) == _nblk,
              "★ 越界张数（99）被夹到「块数」上限，不会切出碎片",
              (len(_sl99), _nblk))
        check(render_detail_slices(plan, {}, out_dir, long_path="") == [],
              "反例：没有长图时切片如实返回空（不炸）")
        check(any("实拍" in n or "抠" in n or "能量" in n for n in r["notes"]),
              "渲染过程如实记录了做法（notes）", r["notes"])

        # ---------- 反例：没有实拍图 → 一张图都不生成，且说清原因 ----------
        r2 = render_all(plan, os.path.join(tmp, "无此文件.png"),
                        os.path.join(tmp, "out2"))
        check(not r2["ok"] and not r2["files"]["main"] and not r2["files"]["crops"],
              "★ 反例：没有实拍图**一张都不生成**（主图不能凭空捏）",
              r2)
        check(bool(r2["reason"]), "失败时给出可读原因", r2["reason"])

        # ---------- 京东版心 ----------
        plan_jd = EC.build_plan("防水背包", platform="jd")
        r3 = render_all(plan_jd, src_path, os.path.join(tmp, "out3"))
        dl3 = load_image(r3["files"]["detail_long"])
        check(dl3 is not None and dl3.width() == 790, "京东详情长图版心 790",
              None if dl3 is None else dl3.width())

        # ---------- 线程无关性：QImage（不是 QPixmap）才敢在 worker 跑 ----------
        # 用 AST 取"真正被引用的名字" —— 直接 grep 源码会被**文档字符串**里的
        # 「不用 QPixmap」这句话误判（本自检第一版就栽在这上面）。
        import ast
        _src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "ecom_render.py"), encoding="utf-8").read()
        _names = {n.id for n in ast.walk(ast.parse(_src)) if isinstance(n, ast.Name)}
        _attrs = {n.attr for n in ast.walk(ast.parse(_src)) if isinstance(n, ast.Attribute)}
        check("QPixmap" not in (_names | _attrs),
              "★ 代码里不含 QPixmap 引用（worker 线程里 QPixmap 会崩）",
              sorted((_names | _attrs) & {"QPixmap"}))
        check("QImage" in _names and "QPainter" in _names,
              "★ 走的是 QImage + QPainter（线程安全的绘制路径）")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("-" * 46)
    print("ecom_render 自检：%d 项，%s" % (passed + failed,
                                          "全部通过" if not failed else "%d 项失败" % failed))
    return 1 if failed else 0


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        raise SystemExit(selftest())
    print("ecom_render.py —— 电商图集真实合成（PySide6 QPainter，零新依赖）")
    print("可用：%s" % available())
    selftest()
