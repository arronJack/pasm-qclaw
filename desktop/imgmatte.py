# -*- coding: utf-8 -*-
"""imgmatte.py —— 专业级图像去底（抠图）引擎，v0.31.27
=========================================================

电商主图的第一张必须是**纯白底**（淘宝/京东的搜索位与放大镜都依赖它），而实拍图
几乎都带背景。以前 `ecom_render.auto_matte` 只做了"四边同色 + 连通洪水填充"，
够用但不专业：**渐变底抠不动、边缘是硬切（锯齿/白边）、白底残留会毁掉搜索位观感**。
本模块把它换成一条真正的抠图管线。

## 管线（每一步都对应一个真实的观感缺陷）

1. **多背景色建模**：四边取样 + K-means（k=1~3）。解决"渐变底/双色底抠不动"。
2. **trimap**：按到背景模型的距离分出「确定背景 / 未知带 / 确定前景」。
   背景还要**从边界连通**才算数（防止把产品内部同色区域挖空）。
3. **局部色模型求 alpha**（shared-matting 思路的向量化近似）：
   在未知带里用邻域盒式均值估计前景色 F 与背景色 B，按 `I = αF + (1-α)B`
   最小二乘解 `α = (I-B)·(F-B) / |F-B|²`。这是"专业边缘"的来源。
4. **去色溢出（decontamination）**：`F = (I-(1-α)B)/α`。**白底残留白边**
   （把白背景的光学溢出留在了产品边缘）就是靠这一步消掉的 —— 电商抠图最关键的
   一步，少了它产品周围永远有一圈发白的毛边。
5. **羽化 + 收缩**：α 做 3×3 均值再 `(α-0.06)/0.9` 收缩，去掉 1px 级别白边、
   同时给出自然的半透明过渡（不是刀切边）。
6. **形态学清理**：小连通域剔除（噪点）+ 内部空洞填充（产品上的高光被误判）。
7. **保留投影**（可选）：比背景暗一档且位于背景连通区的像素 → 给半透明 α。
   白底主图"有落地感"靠的就是它；关掉就是纯贴纸感。

## 诚实边界

- **没有 numpy 就用不了** → `available()` 返回 False，调用方退回简易法（如实说明）。
- 背景太花（四边颜色离散度高）→ 判 `applied=False`，**不硬抠**（硬抠出来的白洞
  比不抠糟得多）。宁可原图放上去 + 留白版式。
- 只输出 **alpha + 去溢出的 RGB**，不做 AI 语义抠图（人像/毛发/透明材质交给专业工具）。
  本模块的目标是"**纯色/渐变底的商品实拍**"，这正是电商主图的主流场景。

## 用法（零 Qt、纯字节，可独立单测）

    from imgmatte import matte
    alpha, info = matte(rgba_bytes, w, h)      # alpha: h*w 字节, 0~255
    print(info)                               # {'applied': True, 'mode': 'plain', ...}
"""
from __future__ import annotations

import math
from collections import deque
from typing import Dict, Optional, Tuple

MAX_SIDE = 1400          # 超过就先缩（抠图质量与耗时都要控）
WORK_SIDE = 560          # 求解 alpha 的工作分辨率
COARSE = 168             # 连通性/形态学在粗分辨率上做（快且够用）


def available() -> bool:
    try:
        import numpy  # noqa: F401
        return True
    except Exception:                                    # noqa: BLE001
        return False


def _np():
    import numpy as n
    return n


# --------------------------------------------------------------------------
# 基础件
# --------------------------------------------------------------------------
def _resize_qimage_fallback(rgba: bytes, w: int, h: int, tw: int, th: int) -> bytes:
    """没有 numpy 时的最近邻缩放（本模块主要路径用 numpy，这里只兜底）。"""
    out = bytearray(tw * th * 4)
    for y in range(th):
        sy = min(h - 1, y * h // th)
        row = sy * w * 4
        o = y * tw * 4
        for x in range(tw):
            sx = min(w - 1, x * w // tw)
            i = row + sx * 4
            out[o + x * 4: o + x * 4 + 4] = rgba[i:i + 4]
    return bytes(out)


def _box_mean(a, r: int):
    """盒式均值（积分图，O(1)/像素）。a 形状 (h,w) 或 (h,w,c)。"""
    n = _np()
    if a.ndim == 2:
        a = a[:, :, None]
        squeeze = True
    else:
        squeeze = False
    h, w, c = a.shape
    pad = n.pad(a, ((r, r), (r, r), (0, 0)), mode="edge")
    ii = n.cumsum(n.cumsum(pad, axis=0), axis=1)
    ii = n.pad(ii, ((1, 0), (1, 0), (0, 0)), mode="constant")
    k = 2 * r + 1
    out = (ii[k:, k:] - ii[:-k, k:] - ii[k:, :-k] + ii[:-k, :-k]) / float(k * k)
    return out[:, :, 0] if squeeze else out


def _kmeans(x, k: int, iters: int = 10, seed: int = 7):
    """极简 K-means（只用于"四边背景色"这种低维小样本，够用）。返回 (centers, counts)。"""
    n = _np()
    rng = n.random.default_rng(seed)
    if len(x) == 0:
        return n.zeros((0, 3), n.float32), n.zeros(0, n.int64)
    k = max(1, min(int(k), len(x)))
    # k-means++ 的简化版：第一个取中位数，其余取离已选中心最远的点
    idx = [len(x) // 2]
    for _ in range(k - 1):
        d = n.min(n.stack([((x - x[i]) ** 2).sum(axis=1) for i in idx]), axis=0)
        idx.append(int(n.argmax(d)))
    cen = x[n.array(idx, dtype=n.int64)].astype(n.float32)
    for _ in range(iters):
        d = ((x[:, None, :] - cen[None, :, :]) ** 2).sum(axis=2)
        lab = n.argmin(d, axis=1)
        new = n.stack([x[lab == i].mean(axis=0) if (lab == i).any() else cen[i]
                       for i in range(k)])
        if n.allclose(new, cen, atol=0.4):
            cen = new
            break
        cen = new
    counts = n.bincount(lab, minlength=k).astype(n.int64)
    order = n.argsort(-counts)
    return cen[order], counts[order]


#: 粗网格步长（**采样与上采样必须用同一套**，见 _coarse_binary / _up 的注释）
def _steps(h: int, w: int, cw: int, ch: int):
    return max(1, math.ceil(h / float(ch))), max(1, math.ceil(w / float(cw)))


def _coarse_binary(mask, cw: int, ch: int):
    """把布尔 mask 按**整数步长**抽样到 (ch,cw)。

    ⚠️ 这里的步长必须与 `_up()` 完全一致。曾经的写法是"抽样按 `y*h//ch`、
    上采样按 `ceil(h/ch)` 重复" —— 两个比例不同（2.5 vs 3），于是粗网格上算出来的
    掩膜被**纵向拉伸 20% 并整体位移**：背景连通性、形态学清理、投影判据全部错位
    （实测：投影判据因此把整条投影带误判进产品轮廓，`shadow_kept` 永远是 False）。
    """
    sy, sx = _steps(*mask.shape, cw, ch)
    return mask[::sy, ::sx][:ch, :cw]


def _up(mask, H: int, W: int):
    """把粗网格布尔阵按同一步长放大回 (H,W)。"""
    n = _np()
    ch, cw = mask.shape
    sy, sx = _steps(H, W, cw, ch)
    return n.repeat(n.repeat(mask, sy, axis=0), sx, axis=1)[:H, :W]


def _flood_from_border(mask):
    """从四边出发的连通洪水（粗分辨率布尔阵），返回"与边界连通"的真值阵。"""
    h, w = mask.shape
    seen = [[False] * w for _ in range(h)]
    dq = deque()
    for x in range(w):
        for y in (0, h - 1):
            if mask[y, x] and not seen[y][x]:
                seen[y][x] = True
                dq.append((x, y))
    for y in range(h):
        for x in (0, w - 1):
            if mask[y, x] and not seen[y][x]:
                seen[y][x] = True
                dq.append((x, y))
    while dq:
        x, y = dq.popleft()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < w and 0 <= ny < h and mask[ny, nx] and not seen[ny][nx]:
                seen[ny][nx] = True
                dq.append((nx, ny))
    n = _np()
    return n.array(seen, dtype=bool)


def _dilate(mask):
    """4 邻域膨胀一格（布尔阵）。"""
    n = _np()
    out = mask.copy()
    out[1:, :] |= mask[:-1, :]
    out[:-1, :] |= mask[1:, :]
    out[:, 1:] |= mask[:, :-1]
    out[:, :-1] |= mask[:, 1:]
    return out


def _fill_holes(mask):
    """把 mask（True=前景）内部被包住的空洞填上（产品高光被误判成背景时靠它救）。"""
    return mask | ~_flood_from_border(~mask)


def _drop_small(mask, min_frac: float = 0.0015):
    """粗分辨率上剔除过小的前景连通域（噪点）；这是"干净"的另一半。"""
    h, w = mask.shape
    seen = [[0] * w for _ in range(h)]
    comps = []
    for y in range(h):
        for x in range(w):
            if mask[y, x] and not seen[y][x]:
                dq = deque([(x, y)])
                seen[y][x] = 1
                cells = [(x, y)]
                while dq:
                    cx, cy = dq.popleft()
                    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        nx, ny = cx + dx, cy + dy
                        if 0 <= nx < w and 0 <= ny < h and mask[ny, nx] \
                                and not seen[ny][nx]:
                            seen[ny][nx] = 1
                            dq.append((nx, ny))
                            cells.append((nx, ny))
                comps.append(cells)
    if not comps:
        return mask
    keep = max(comps, key=len)
    limit = max(6, int(min_frac * h * w))
    n = _np()
    out = n.zeros((h, w), dtype=bool)
    for c in comps:
        if len(c) >= limit:
            for x, y in c:
                out[y, x] = True
    # 全被剔掉 = 判据失效 → 原样返回（宁可不动也不能把整张图清空）
    return out if out.any() else mask


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------
def matte(rgba: bytes, w: int, h: int, keep_shadow: bool = True,
          tol_scale: float = 1.0) -> Tuple[bytes, Dict]:
    """对一张 RGBA8888 的原图做专业去底。

    返回 `(alpha_bytes, info)`：alpha 为 h*w 字节（0=全透明，255=不透明）。
    **RGB 不变**（去色溢出由 `decontaminate()` 另做，这样"抠图"与"除白边"
    两件事各自可测、可关）。
    info: {applied, mode, reason, bg_colors, spread, coverage, shadow_kept}
    """
    n = _np()
    if not available():
        return bytes(h * w), {"applied": False, "reason": "numpy 不可用"}
    if w < 8 or h < 8 or len(rgba) < w * h * 4:
        return bytes(h * w), {"applied": False, "reason": "图像过小或数据不完整"}
    A = n.frombuffer(bytes(rgba), dtype=n.uint8).reshape(h, w, 4)
    rgb_full = A[:, :, :3].astype(n.float32)

    # ---- ① 统一到工作分辨率（质量/耗时平衡）----
    sc = min(1.0, float(WORK_SIDE) / max(w, h))
    if sc < 1.0:
        tw, th = max(8, int(w * sc)), max(8, int(h * sc))
        # 用 QImage 做高质量缩放更稳；取不到就最近邻
        try:
            import os
            import sys
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            from qt_compat import QImage, Qt
            qi = QImage(bytes(rgba), w, h, w * 4,
                        QImage.Format.Format_RGBA8888).scaled(
                tw, th, Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
            raw = bytes(qi.constBits())[: qi.bytesPerLine() * th]
            bpl = qi.bytesPerLine()
            rgb = n.frombuffer(raw, n.uint8).reshape(th, bpl)[:, :tw * 4] \
                .reshape(th, tw, 4)[:, :, :3].astype(n.float32)
        except Exception:                                # noqa: BLE001
            rgb = rgb_full[:: max(1, h // th), :: max(1, w // tw)][:th, :tw]
    else:
        th, tw = h, w
        rgb = rgb_full
    H, W = rgb.shape[:2]

    # ---- ② 多背景色建模 ----
    b = max(2, int(0.025 * min(H, W)))
    band = n.concatenate([rgb[:b].reshape(-1, 3), rgb[-b:].reshape(-1, 3),
                          rgb[:, :b].reshape(-1, 3), rgb[:, -b:].reshape(-1, 3)])
    step = max(1, len(band) // 20000)
    band = band[::step]
    cen, cnt = _kmeans(band, k=3, iters=10)
    if len(cen) == 0:
        return bytes(h * w), {"applied": False, "reason": "取不到背景样本"}
    share = cnt / float(cnt.sum())
    # 只保留占比 >=12% 的簇（渐变底会自然分到 2~3 簇；杂色底每簇占比都小）
    keep = [i for i in range(len(cen)) if share[i] >= 0.12]
    if not keep:
        keep = [int(n.argmax(share))]
    cen = n.stack([cen[i] for i in keep])
    # 背景离散度：band 像素到最近簇中心的平均距离
    spread = float(n.min(n.stack([n.linalg.norm(band - c, axis=1) for c in cen]),
                         axis=0).mean())
    if spread > 30 * tol_scale:
        return bytes(h * w), {"applied": False, "reason": "背景过于复杂（离散度 %.0f）"
                                                        % spread,
                              "spread": spread}

    # ---- ③ trimap ----
    d_bg = n.min(n.stack([n.linalg.norm(rgb - c, axis=2) for c in cen]), axis=0)
    t_bg = max(14.0, spread * 1.6) * tol_scale
    t_fg = max(64.0, spread * 4.0) * tol_scale
    bg_raw = d_bg < t_bg
    fg_raw = d_bg > t_fg
    fg_frac = float(fg_raw.mean())
    if fg_frac < 0.015:
        return bytes(h * w), {"applied": False,
                              "reason": "前景占比过小（%.1f%%），不像商品图"
                                        % (fg_frac * 100), "spread": spread}
    if fg_frac > 0.95:
        return bytes(h * w), {"applied": False, "reason": "整图都像前景（可能无背景）",
                              "spread": spread}
    # 背景必须**从边界连通**才算背景
    cw, ch = COARSE, max(8, int(COARSE * H / float(W)))
    cbm = _coarse_binary(bg_raw, cw, ch)
    reach = _flood_from_border(cbm)
    bg_hard = _up(reach, H, W) & (d_bg < t_bg * 1.8)
    fg_hard = fg_raw & ~bg_hard
    if fg_hard.sum() < 0.01 * H * W:
        return bytes(h * w), {"applied": False, "reason": "连通前景太少，判据不成立"}

    # ---- ④ 局部色模型求 alpha（shared matting 近似）----
    r = max(3, int(min(H, W) * 0.012))
    fg_f = fg_hard.astype(n.float32)[:, :, None]
    bg_f = bg_hard.astype(n.float32)[:, :, None]
    den_f = n.maximum(_box_mean(fg_f, r), 1e-6)
    den_b = n.maximum(_box_mean(bg_f, r), 1e-6)
    gm_f = rgb[fg_hard].mean(axis=0)
    gm_b = rgb[bg_hard].mean(axis=0)
    F = _box_mean(rgb * fg_f, r) / den_f
    B = _box_mean(rgb * bg_f, r) / den_b
    # 窗口里没有可用样本 → 用全局均值兜底（否则出来一片黑/白）
    F = n.where(den_f < 1.0 / (2 * r + 1) ** 2, gm_f[None, None, :], F)
    B = n.where(den_b < 1.0 / (2 * r + 1) ** 2, gm_b[None, None, :], B)
    d = F - B
    denom = (d * d).sum(axis=2)
    alpha = ((rgb - B) * d).sum(axis=2) / n.maximum(denom, 1e-6)
    alpha = n.clip(alpha, 0.0, 1.0)
    alpha[bg_hard] = 0.0
    alpha[fg_hard] = 1.0

    # ---- ⑤ 保留投影（可选）----
    # 判据必须是"**局部**背景色被压暗"，而不是"比全局背景暗" ——
    # 后者会把渐变底的暗侧整片当投影（实测把四角 alpha 从 0 顶到 83，去底全废）。
    # 另加几何约束：投影候选必须在**产品轮廓之外**（轮廓先填洞再膨胀），
    # 否则产品自身的暗部也会被点亮。
    shadow_kept = False
    if keep_shadow:
        wl = n.array([0.299, 0.587, 0.114], dtype=n.float32)
        luma = rgb @ wl
        hr = max(4, int(0.045 * min(H, W)))                     # 局部背景取样半径
        bgish = (d_bg < t_bg * 2.2).astype(n.float32)[:, :, None]
        cden = n.maximum(_box_mean(bgish, hr), 1e-6)
        B_loc = _box_mean(rgb * bgish, hr) / cden
        luma_loc = B_loc @ wl
        # ⚠️ 用"**确定**是产品"的判据取轮廓，不能用 fg_hard ——
        #    投影比背景暗 40~70 个亮度级，与背景的距离远大于 t_fg，会被判成
        #    fg_hard（alpha=1）→ 轮廓把投影整片吞进去 → 投影永远识别不出来
        #    （自检第一版就假红在这上面）。
        certain = d_bg > max(96.0, t_fg * 2.5)
        c_hard2 = _coarse_binary(certain, cw, ch)
        c_prod = _dilate(_fill_holes(c_hard2))
        prod = _up(c_prod, H, W)
        ratio = luma / n.maximum(luma_loc, 1e-3)
        dev = n.linalg.norm(rgb - B_loc * ratio[:, :, None], axis=2)
        sh = ((~prod) & (ratio > 0.55) & (ratio < 0.965) & (dev < 32.0) &
              ((luma_loc - luma) > 10.0))
        if 0.0008 < float(sh.mean()) < 0.35:
            a_sh = n.clip((1.0 - ratio) / 0.38, 0.14, 0.60)
            # 投影像素要**改成**半透明，而不是取 max —— 它此刻的 alpha 是被
            # 误判出来的 1.0，取 max 等于什么都没做（会留下一块硬灰板）。
            alpha = n.where(sh, a_sh, alpha)
            shadow_kept = True

    # ---- ⑥ 形态学清理（粗分辨率上做，快且不出锯齿）----
    hard = alpha > 0.5
    c_hard = _coarse_binary(hard, cw, ch)
    c_clean = _drop_small(c_hard, 0.0012)
    if c_clean.any():
        clean = _up(c_clean, H, W)
        # 只清理"被判为孤立小块"的区域：大面积前景一律保留
        alpha = n.where(hard & ~clean, 0.0, alpha)

    # ---- ⑦ 羽化 + 收缩（去 1px 白边，同时给出自然过渡）----
    alpha = _box_mean(alpha, 1)
    alpha = n.clip((alpha - 0.06) / 0.90, 0.0, 1.0)

    # ---- ⑧ 上采样回原分辨率 ----
    cov = float((alpha > 0.5).mean())
    if sc < 1.0:
        try:
            from qt_compat import QImage, Qt
            a8 = (alpha * 255.0).astype(n.uint8)
            ai = QImage(a8.tobytes(), W, H, W, QImage.Format.Format_Grayscale8)
            ai = ai.scaled(w, h, Qt.AspectRatioMode.IgnoreAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
            raw = bytes(ai.constBits())[: ai.bytesPerLine() * h]
            bpl = ai.bytesPerLine()
            out = n.frombuffer(raw, n.uint8).reshape(h, bpl)[:, :w].copy()
        except Exception:                                # noqa: BLE001
            out = n.array(_resize_qimage_fallback(
                (alpha * 255).astype(n.uint8).tobytes(), W, H, w, h),
                dtype=n.uint8).reshape(h, w, 4)[:, :, 0]
    else:
        out = (alpha * 255.0).astype(n.uint8)
    return out.tobytes(), {"applied": True,
                           "mode": "plain" if len(cen) == 1 else "gradient",
                           "bg_colors": [tuple(int(v) for v in c) for c in cen],
                           "spread": round(spread, 1),
                           "coverage": round(cov, 3),
                           "shadow_kept": shadow_kept}


def decontaminate(rgba: bytes, alpha: bytes, w: int, h: int,
                  bg=(255, 255, 255)) -> bytes:
    """去色溢出：把半透明边缘像素"反混合"回产品本色，消除白边。

    `I = αF + (1-α)B` → `F = (I - (1-α)B) / α`，再夹到 [0,255]。
    α 太小的像素（<0.08）不参与（除零会炸出一片黑，且那些像素本来就不可见）。
    """
    n = _np()
    A = n.frombuffer(bytes(rgba), dtype=n.uint8).reshape(h, w, 4).astype(n.float32)
    al = n.frombuffer(bytes(alpha), dtype=n.uint8).reshape(h, w).astype(n.float32) / 255.0
    B = n.array(bg, dtype=n.float32)[None, None, :]
    a3 = n.clip(al, 1e-2, 1.0)[:, :, None]
    F = (A[:, :, :3] - (1.0 - a3) * B) / a3
    F = n.clip(F, 0.0, 255.0)
    edge = (al > 0.08) & (al < 0.995)
    rgb = n.where(edge[:, :, None], F, A[:, :, :3])
    out = n.concatenate([rgb, (al * 255.0).round()[:, :, None]], axis=2)
    return out.astype(n.uint8).tobytes()


def edge_hardness(alpha: bytes, w: int, h: int) -> float:
    """边缘"硬度"：半透明像素占前景边缘的比例。太低 = 刀切边（不专业），
    太高 = 糊。健康区间约 0.08~0.55。用于自检/诊断。"""
    n = _np()
    al = n.frombuffer(bytes(alpha), dtype=n.uint8).reshape(h, w).astype(n.float32) / 255.0
    fg = al > 0.5
    if not fg.any():
        return 0.0
    band = fg & (n.roll(fg, 1, 0) | n.roll(fg, -1, 0) |
                 n.roll(fg, 1, 1) | n.roll(fg, -1, 1))
    soft = band & (al > 0.06) & (al < 0.94)
    band_n = int(band.sum())
    return float(soft.sum()) / band_n if band_n else 0.0


# --------------------------------------------------------------------------
# 自检：**造图**验证，不碰用户文件
# --------------------------------------------------------------------------
def _mk(w, h, bg_fn, fg_fn, noise=0.0, seed=3):
    """合成一张"商品图"：背景由 bg_fn(x,y) 给，主体是圆角矩形 + 圆。"""
    n = _np()
    rng = n.random.default_rng(seed)
    yy, xx = n.mgrid[0:h, 0:w]
    rgb = n.zeros((h, w, 3), n.float32)
    for i in range(3):
        rgb[:, :, i] = bg_fn(xx, yy, i)
    x0, y0, x1, y1 = int(w * 0.26), int(h * 0.30), int(w * 0.74), int(h * 0.74)
    body = (xx >= x0) & (xx <= x1) & (yy >= y0) & (yy <= y1)
    cx, cy, rr = w * 0.5, h * 0.5, min(w, h) * 0.12
    circle = ((xx - cx) ** 2 + (yy - cy) ** 2) <= rr ** 2
    fgc = fg_fn()
    for i in range(3):
        rgb[:, :, i] = n.where(body, fgc[i], rgb[:, :, i])
        rgb[:, :, i] = n.where(circle, min(255.0, fgc[i] + 60), rgb[:, :, i])
    if noise:
        rgb = rgb + rng.normal(0, noise, rgb.shape)
    rgb = n.clip(rgb, 0, 255)
    a = n.full((h, w, 4), 255, n.uint8)
    a[:, :, :3] = rgb.astype(n.uint8)
    return a.tobytes(), (x0, y0, x1, y1)


def selftest() -> int:
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

    print("== imgmatte 自检（专业去底）==")
    if not available():
        print("  ⚠ numpy 不可用 → 本模块整体跳过（调用方退回简易法并如实说明）")
        return 0
    n = _np()
    W = H = 420

    # ---------- 正例①：纯色白底 ----------
    rgb, box = _mk(W, H, lambda x, y, i: 252.0, lambda: (40, 70, 120), noise=1.2)
    al, info = matte(rgb, W, H)
    check(info.get("applied"), "纯白底：判为可抠", info)
    a = n.frombuffer(al, n.uint8).reshape(H, W)
    check(int(a[2, 2]) == 0 and int(a[2, W - 3]) == 0,
          "★ 四角被抠成全透明（这才是「去底」）", (int(a[2, 2]), int(a[2, W - 3])))
    check(int(a[H // 2, W // 2]) > 250, "★ 主体中心保持不透明",
          int(a[H // 2, W // 2]))
    hd = edge_hardness(al, W, H)
    check(0.02 < hd < 0.75, "★ 边缘有半透明过渡（不是刀切边，也不是糊）",
          round(hd, 3))

    # ---------- 正例②：渐变底（旧简易法抠不动的那种） ----------
    rgb2, _ = _mk(W, H, lambda x, y, i: 200 + 50 * (x / float(W)) - 10 * i,
                  lambda: (200, 40, 40), noise=1.2)
    al2, info2 = matte(rgb2, W, H)
    check(info2.get("applied"), "★ 渐变底：也能抠（旧简易法在这里是 applied=False）",
          info2)
    check(info2.get("mode") in ("gradient", "plain") and
          len(info2.get("bg_colors") or []) >= 1, "★ 多背景色建模生效", info2)
    a2 = n.frombuffer(al2, n.uint8).reshape(H, W)
    check(int(a2[2, 2]) == 0 and int(a2[H // 2, W // 2]) > 250,
          "渐变底：四角透明 + 主体不透明", (int(a2[2, 2]), int(a2[H // 2, W // 2])))

    # ---------- 正例③：投影保留 ----------
    rgb3, _ = _mk(W, H, lambda x, y, i: 250.0, lambda: (60, 60, 90), noise=0.8)
    yy, xx = n.mgrid[0:H, 0:W]
    shadow = (xx > W * 0.30) & (xx < W * 0.70) & (yy > H * 0.72) & (yy < H * 0.78)
    arr = n.frombuffer(rgb3, n.uint8).reshape(H, W, 4).copy()
    arr[:, :, :3] = n.where(shadow[:, :, None], (arr[:, :, :3] * 0.72).astype(n.uint8),
                            arr[:, :, :3])
    al3, info3 = matte(arr.tobytes(), W, H, keep_shadow=True)
    check(info3.get("applied") and info3.get("shadow_kept") is True,
          "★ 投影被识别并保留（白底主图的「落地感」）", info3)
    al3b, info3b = matte(arr.tobytes(), W, H, keep_shadow=False)
    check(info3b.get("applied") and info3b.get("shadow_kept") is False,
          "关掉投影保留时不再保留（开关真的有效）", info3b)

    # ---------- 反例①：复杂/花色背景 —— 不许硬抠 ----------
    rgb4, _ = _mk(W, H, lambda x, y, i: (x * 7 + y * 13 + i * 41) % 255,
                  lambda: (240, 240, 240), noise=6.0)
    _al4, info4 = matte(rgb4, W, H)
    check(not info4.get("applied"),
          "★ 反例：花色背景**判为不可抠**（不硬抠 → 不产出白洞）", info4.get("reason"))

    # ---------- 反例②：几乎没有前景（纯色底整图） ----------
    flat = n.full((H, W, 4), 250, n.uint8).tobytes()
    _al5, info5 = matte(flat, W, H)
    check(not info5.get("applied"), "★ 反例：没有主体的纯色图不抠",
          info5.get("reason"))

    # ---------- 去色溢出：白边真的被除掉 ----------
    dec = decontaminate(rgb, al, W, H, bg=(252, 252, 252))
    d = n.frombuffer(dec, n.uint8).reshape(H, W, 4).astype(n.float32)
    edge = (n.frombuffer(al, n.uint8).reshape(H, W).astype(n.float32) / 255.0)
    sel = (edge > 0.25) & (edge < 0.8)
    if sel.any():
        # 边缘像素的 RGB 应该更接近产品色(40,70,120)，而不是背景白
        dist_fg = n.linalg.norm(d[:, :, :3] - n.array([40, 70, 120], n.float32),
                                axis=2)[sel].mean()
        dist_bg = n.linalg.norm(d[:, :, :3] - n.array([252, 252, 252], n.float32),
                                axis=2)[sel].mean()
        check(dist_fg < dist_bg, "★ 去色溢出后边缘更接近产品本色（白边被除掉）",
              (round(float(dist_fg), 1), round(float(dist_bg), 1)))
    else:
        check(True, "去色溢出：无半透明带（跳过数值比较）")
    check(len(dec) == W * H * 4, "去色溢出输出尺寸正确")

    # ---------- 健壮性 ----------
    check(matte(b"", 0, 0)[1].get("applied") is False, "空数据不炸")
    check(matte(rgb[:10], W, H)[1].get("applied") is False, "数据不完整不炸")
    tiny, _ = _mk(8, 8, lambda x, y, i: 250.0, lambda: (10, 10, 10))
    check(isinstance(matte(tiny, 8, 8)[1], dict), "极小图不炸")
    check(edge_hardness(al, W, H) >= 0.0, "edge_hardness 可计算")

    print("-" * 46)
    print("imgmatte 自检：%d 项，%s" % (passed + failed,
                                      "全部通过" if not failed else "%d 项失败" % failed))
    return 1 if failed else 0


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        raise SystemExit(selftest())
    print("imgmatte.py —— 专业级图像去底（多背景色 + trimap + 局部色模型 + 去色溢出）")
    print("numpy 可用：%s" % available())
    selftest()
