# -*- coding: utf-8 -*-
"""AI 商品图精修（v0.31.31）：把**实拍图**变成能上架的干净商品图。

为什么要有这个模块
------------------
实拍图里往往有手、有桌面、有水印。真机事故（2026-10-14，小志手托项链的实拍图）：
白底主图里**手一直在画面里**，平台搜索位要的是"只有产品的纯白底"。
而几何裁切/抠图在"手与细链条重叠"时物理上抠不干净 ——
`imgmatte.matte_product()` 对着这类图连试 6 轮，判据全部判"不可靠"。

图像编辑模型听得懂"去掉手、换纯白底、产品保持原样"。实测（Qwen-Image-Edit-2509）：

| 指标 | 源图(手托) | AI 编辑后 | 旧"白底主图" |
|---|---|---|---|
| 手/皮肤占比 | 62.8% | **0.00%** | 28.6% |
| 画面非白占比 | 97.3% | **3.4%** | 46.7% |

配套两件事（模型输出的固有毛病，都在本模块处理，可单测）：
  · `strip_watermark()` —— 免费额度图右下角有淡灰「AI生成」水印 → 检测后填白
  · `to_square_on_white()` —— `image_size` 参数不生效（输出保持原比例）
    → 取产品包围盒居中贴到 800×800 纯白画布

纪律（写进代码，不靠记性）
--------------------------
1. **AI 精修图永远与实拍合成版并存**（命名 `_AI` / `_实拍`），绝不覆盖实拍图 ——
   平台对"图文不符"有判罚，选哪张上架是用户的决定，我们只负责如实标注。
2. 没引擎 / 调用失败 → 返回 `None`，调用方回退实拍版（**绝不假装出了图**）。
3. 每次调用都记 notes：用了哪个模型、哪张是 AI 精修、水印清了几处。

零新增依赖：复用 `creators._http_json` / `_save_b64_or_url`（纯 urllib）+ qt_compat + numpy。
"""
from __future__ import annotations

import os
import sys
import time
from typing import Callable, Dict, List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

#: 各类图的提示词模板。`{product}` 会替换成产品名（如「项链」）。
#: 写法遵循图像编辑模型的脾气：**先说要什么，再强调要保住什么**。
PROMPTS: Dict[str, str] = {
    "白底": (
        "将画面中的{product}完整提取出来，单独放在纯白色背景上"
        "（背景必须是纯白 #FFFFFF，画面中**只保留{product}本身**）；"
        "去掉托着它的手/手指/皮肤、去掉原背景与原边框、去掉画面中所有文字与水印；"
        "{product}的形状、结构、材质、颜色必须与原图完全一致，不要增删任何配件；"
        "专业商品商业摄影棚拍，柔和均匀打光，产品居中，四周留出均匀留白"
    ),
    "场景": (
        "把画面中的{product}完整提取出来，放在干净的高端展示台面上做商品展示图；"
        "去掉手/手指/皮肤、去掉原背景与边框、去掉画面中所有文字与水印；"
        "{product}的形状、结构、材质完全保持原样；"
        "柔和自然光、浅景深、背景简洁{scene}，正方形构图，产品居中"
    ),
    "礼盒": (
        "把画面中的{product}完整提取出来，放在一个深色丝绒首饰礼盒上展示；"
        "去掉手/手指/皮肤、去掉原背景与边框、去掉画面中所有文字与水印；"
        "{product}的形状、结构、材质完全保持原样；"
        "高端商业摄影，柔和打光，浅景深，画面高级干净，正方形构图"
    ),
    "柔光": (
        "把画面中的{product}完整提取出来，放在柔和的浅色渐变背景上（奶油色到米白）；"
        "去掉手/手指/皮肤、去掉原背景与边框、去掉画面中所有文字与水印；"
        "{product}的形状、结构、材质完全保持原样；"
        "柔光箱打光，光影柔和通透，高级氛围感，产品居中，正方形构图"
    ),
    "45度": (
        "把画面中的{product}完整提取出来，做成 45 度俯视的商品展示图，纯白或极浅灰背景；"
        "去掉手/手指/皮肤、去掉原背景与边框、去掉画面中所有文字与水印；"
        "{product}的形态、结构、材质完全保持原样；"
        "专业棚拍打光，立体感强，产品居中，正方形构图"
    ),
    "佩戴": (
        "把画面中的{product}做成真人佩戴的展示图：只出现佩戴部位的局部（颈部/耳部/手部），"
        "构图干净，皮肤自然细腻，不露脸；去掉原图的手托姿势与原背景、去掉所有文字与水印；"
        "{product}的形状、结构、材质必须与原图完全一致；"
        "自然光人像商品摄影，浅景深，氛围高级，正方形构图"
    ),
    "细节": (
        "把画面中{product}的{part}放大做超近特写：{product}的形态与材质必须与原图完全一致，"
        "只做构图与打光的提升；去掉手/手指/皮肤、去掉原背景、去掉所有文字与水印；"
        "显微级细节清晰，专业微距商品摄影，柔和打光，突出工艺质感，正方形构图"
    ),
    "详情封面": (
        "以画面中的{product}为主体，做一张电商详情页的主视觉封面图："
        "{product}保持原样完整清晰，去掉手/手指/皮肤与原背景、去掉所有文字与水印；"
        "背景为高级的柔和渐变或极简布景，打光精致，氛围高级，画面留出上方标题空间，"
        "竖版构图（3:4）"
    ),
    "详情场景": (
        "以画面中的{product}为主体，做一张电商详情页的场景图："
        "{product}保持原样完整清晰，去掉手/手指/皮肤与原背景、去掉所有文字与水印；"
        "真实使用场景氛围，{scene}，暖调光线，生活气质感，竖版构图（3:4）"
    ),
    "详情卖点": (
        "以画面中的{product}为主体，做一张电商详情页的卖点图："
        "{product}保持原样完整清晰，与干净的几何色块/光影构图结合，"
        "去掉手/手指/皮肤与原背景、去掉所有文字与水印；"
        "现代感设计排版，专业高级，图片上下留出文字区，竖版构图（3:4）"
    ),
}


def prompt_for(kind: str, product: str = "产品", part: str = "工艺细节",
               scene: str = "") -> str:
    """取某类图的完整提示词（未知 kind 走「场景」）。"""
    tpl = PROMPTS.get(kind) or PROMPTS["场景"]
    return (tpl.replace("{product}", str(product or "产品"))
               .replace("{part}", str(part or "工艺细节"))
               .replace("{scene}", ("，" + str(scene)) if scene else ""))


# --------------------------------------------------------------------------
# 引擎
# --------------------------------------------------------------------------
def resolve_conf(cfg: dict) -> Optional[dict]:
    """拿一个能用的图像引擎配置（优先 image，退而 vision —— 都是 OpenAI 兼容）。"""
    try:
        import creators as CRE
        for kind in ("image", "vision"):
            conf = CRE.get_engine(cfg or {}, kind)
            if conf and conf.get("base_url") and conf.get("api_key"):
                return conf
    except Exception:                                        # noqa: BLE001
        return None
    return None


def default_model(conf: dict) -> str:
    """编辑模型：优先 Qwen-Image-Edit 系列（专门做图像编辑），否则用已配的模型。"""
    m = str((conf or {}).get("model") or "")
    if "edit" in m.lower():
        return m
    return "Qwen/Qwen-Image-Edit-2509"


def available(cfg: dict) -> bool:
    """AI 精修是否可用（配了引擎就算可用；真调用失败再回退）。"""
    return resolve_conf(cfg) is not None


# --------------------------------------------------------------------------
# 图像处理（纯本地，可离线单测）
# --------------------------------------------------------------------------
def _numpy():
    import numpy as np
    return np


def _qimg(path: str):
    """读图 → (QImage RGBA8888, numpy 数组 h*w*3)。"""
    from qt_compat import QImage
    img = QImage(path)
    if img.isNull():
        raise RuntimeError("读不到图片：" + str(path))
    img = img.convertToFormat(QImage.Format.Format_RGBA8888)
    w, h = img.width(), img.height()
    n = _numpy()
    A = n.frombuffer(bytes(img.constBits())[:w * h * 4],
                     dtype=n.uint8).reshape(h, w, 4)
    return img, A[:, :, :3].astype(n.uint8), n


def _save_rgb(rgb, out_path: str):
    from qt_compat import QImage
    n = _numpy()
    a = n.ascontiguousarray(rgb.astype(n.uint8))
    h, w = a.shape[:2]
    img = QImage(a.tobytes(), w, h, w * 3, QImage.Format.Format_RGB888)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    if not img.save(out_path, os.path.splitext(out_path)[1].lstrip(".").upper() or "PNG"):
        raise RuntimeError("图片写盘失败：" + str(out_path))
    return out_path


def strip_watermark(rgb, region: Tuple[float, float, float, float] = (0.55, 0.82, 1.0, 1.0),
                    bbox_frac: float = 0.22, area_frac: float = 0.012) -> Tuple[object, int]:
    """把角落的淡灰水印**填白**（免费额度图自带「AI生成」）。

    判据必须是"孤立小块"而不是"某块区域发灰" —— 否则产品伸到角落时会被误抹。
    返回 `(新数组, 清除的小块数)`：`0` 表示没找到水印（不是错误）。
    """
    n = _numpy()
    out = rgb.copy()
    h, w = out.shape[:2]
    x0, y0 = int(region[0] * w), int(region[1] * h)
    sub = out[y0:, x0:]
    if sub.size == 0:
        return out, 0
    mn = sub.min(axis=2).astype(n.int16)
    mx = sub.max(axis=2).astype(n.int16)
    # 淡灰字：不太黑也不太白、且低饱和（彩色产品不会被选中）
    cand = (mn > 120) & (mx < 250) & ((mx - mn) < 26)
    if not cand.any():
        return out, 0
    # 连通域（4 邻域 BFS，块很小所以很快）
    hh, ww = cand.shape
    seen = n.zeros((hh, ww), dtype=bool)
    removed = 0
    limit_bbox = bbox_frac * w
    for y in range(hh):
        row = cand[y]
        for x in n.where(row & ~seen[y])[0]:
            if seen[y, x]:
                continue
            stack = [(int(x), int(y))]
            seen[y, x] = True
            cells: List[Tuple[int, int]] = []
            while stack:
                cx, cy = stack.pop()
                cells.append((cx, cy))
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nx, ny = cx + dx, cy + dy
                    if 0 <= nx < ww and 0 <= ny < hh and cand[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        stack.append((nx, ny))
            if len(cells) > area_frac * w * h:
                continue                                  # 太大 → 那是产品/阴影，别动
            xs = [c[0] for c in cells]
            ys = [c[1] for c in cells]
            if (max(xs) - min(xs) + 1) > limit_bbox:
                continue                                  # 太长 → 是产品边缘，别动
            for cx, cy in cells:
                out[y0 + cy, x0 + cx] = 255               # 水印下面就是白底 → 填白
            removed += 1
    return out, removed


def nonwhite_mask(rgb, thr: int = 245):
    n = _numpy()
    return rgb.min(axis=2) < int(thr)


def product_bbox(rgb, thr: int = 245) -> Optional[Tuple[int, int, int, int]]:
    """产品包围盒（非白像素）；全白 → None。"""
    n = _numpy()
    m = nonwhite_mask(rgb, thr)
    ys, xs = n.where(m)
    if len(xs) == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())


def coverage(rgb, thr: int = 245) -> float:
    """产品在画面里的占比（越小越"干净"，纯白底商品图通常 2%~25%）。"""
    return float(nonwhite_mask(rgb, thr).mean())


def to_square_on_white(rgb, size: int = 800, fill: float = 0.84,
                       pad: float = 0.16) -> object:
    """产品包围盒居中 → 贴到 `size×size` 纯白画布（等比缩放，长边占 `fill`）。"""
    n = _numpy()
    from qt_compat import QImage
    bb = product_bbox(rgb)
    if bb is None:
        raise RuntimeError("整张是白的，没有产品可构图")
    x0, y0, x1, y1 = bb
    bw, bh = x1 - x0 + 1, y1 - y0 + 1
    p = int(max(bw, bh) * pad)
    h, w = rgb.shape[:2]
    cx0, cy0 = max(0, x0 - p), max(0, y0 - p)
    cx1, cy1 = min(w, x1 + 1 + p), min(h, y1 + 1 + p)
    crop = n.ascontiguousarray(rgb[cy0:cy1, cx0:cx1])
    ch, cw = crop.shape[:2]
    qi = QImage(crop.tobytes(), cw, ch, cw * 3, QImage.Format.Format_RGB888)
    sc = (size * float(fill)) / float(max(cw, ch))
    tw, th = max(1, int(round(cw * sc))), max(1, int(round(ch * sc)))
    qi = qi.scaled(tw, th)
    raw = bytes(qi.constBits())[:qi.bytesPerLine() * th]
    small = n.frombuffer(raw, n.uint8).reshape(th, qi.bytesPerLine())[:, :tw * 3] \
        .reshape(th, tw, 3)
    canvas = n.full((size, size, 3), 255, dtype=n.uint8)
    ox, oy = (size - tw) // 2, (size - th) // 2
    canvas[oy:oy + th, ox:ox + tw] = small
    return canvas


def postprocess(path: str, out_path: str, size: int = 800,
                log: Optional[Callable[[str], None]] = None) -> dict:
    """把模型输出收拾成能上架的图：去水印 → 包围盒居中 → 方图。

    返回 `{ok, wm_removed, coverage_before, coverage_after, path, reason}`
    `ok=False` 表示这张图"不像纯白底商品图"（背景没换干净），调用方应如实说明。
    """
    def _say(m):
        if callable(log):
            try:
                log(m)
            except Exception:                                # noqa: BLE001
                pass
    try:
        _img, rgb, n = _qimg(path)
    except Exception as ex:                                  # noqa: BLE001
        return {"ok": False, "reason": "读图失败：%s" % ex}
    cov0 = coverage(rgb)
    rgb2, wm = strip_watermark(rgb)
    try:
        sq = to_square_on_white(rgb2, size=size)
    except Exception as ex:                                  # noqa: BLE001
        return {"ok": False, "reason": str(ex), "wm_removed": wm}
    try:
        _save_rgb(sq, out_path)
    except Exception as ex:                                  # noqa: BLE001
        return {"ok": False, "reason": str(ex), "wm_removed": wm}
    cov1 = coverage(sq)
    if wm:
        _say("已清除右下角水印 %d 处" % wm)
    if cov1 > 0.45:
        return {"ok": False, "wm_removed": wm, "coverage_before": round(cov0, 3),
                "coverage_after": round(cov1, 3), "path": out_path,
                "reason": "画面里非产品像素仍占 %.0f%%，不像纯白底商品图" % (cov1 * 100)}
    return {"ok": True, "wm_removed": wm, "coverage_before": round(cov0, 3),
            "coverage_after": round(cov1, 3), "path": out_path}


# --------------------------------------------------------------------------
# 调模型
# --------------------------------------------------------------------------
def edit_product(cfg: dict, src_path: str, out_path: str, kind: str = "白底",
                 product: str = "产品", part: str = "工艺细节", scene: str = "",
                 timeout: float = 300.0,
                 log: Optional[Callable[[str], None]] = None) -> Optional[str]:
    """让图像编辑模型按 `kind` 精修实拍图，落盘到 `out_path`。

    任何失败（没引擎/网络/返回异常）→ 返回 None（调用方回退实拍版）。
    """
    def _say(m):
        if callable(log):
            try:
                log(m)
            except Exception:                                # noqa: BLE001
                pass
    if not (src_path and os.path.isfile(src_path)):
        return None
    conf = resolve_conf(cfg)
    if not conf:
        return None
    try:
        import base64
        import json
        import urllib.request
        import creators as CRE
        base = str(conf.get("base_url") or "").rstrip("/")
        key = str(conf.get("api_key") or "")
        model = default_model(conf)
        with open(src_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        ext = os.path.splitext(src_path)[1].lower()
        mime = "image/jpeg" if ext in (".jpg", ".jpeg") else (
            "image/webp" if ext == ".webp" else "image/png")
        payload = {
            "model": model,
            "prompt": prompt_for(kind, product, part, scene),
            "image": "data:%s;base64,%s" % (mime, b64),
            "num_inference_steps": 30,
            "guidance_scale": 7.5,
            "image_size": "1024x1024",        # 该模型不严格生效，方图在 postprocess 里做
        }
        t0 = time.time()
        out = CRE._http_json(base + "/images/generations", payload, key, timeout=timeout)
        items = out.get("data") or out.get("images") or []
        if not items:
            _say("（AI 精修没返回图片）")
            return None
        os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
        CRE._save_b64_or_url(items[0], out_path)
        _say("AI 精修完成（%s · %.0fs）" % (model.split("/")[-1], time.time() - t0))
        return out_path if os.path.isfile(out_path) else None
    except Exception as ex:                                  # noqa: BLE001
        _say("（AI 精修没成功：%s）" % str(ex)[:120])
        return None


def refine(cfg: dict, src_path: str, out_path: str, kind: str = "白底",
           product: str = "产品", part: str = "工艺细节", scene: str = "",
           size: int = 800, log: Optional[Callable[[str], None]] = None) -> Optional[str]:
    """一步到位：编辑 + 去水印 + 方图。失败返回 None。"""
    tmp = out_path + ".raw.png"
    got = edit_product(cfg, src_path, tmp, kind=kind, product=product,
                       part=part, scene=scene, log=log)
    if not got:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return None
    info = postprocess(got, out_path, size=size, log=log)
    try:
        os.remove(tmp)
    except OSError:
        pass
    if not info.get("ok"):
        if callable(log):
            log("（AI 精修结果不合格：%s → 该张回退实拍版）" % info.get("reason"))
        try:
            os.remove(out_path)
        except OSError:
            pass
        return None
    return out_path


# --------------------------------------------------------------------------
# 自检（**离线**，绝不联网）
# --------------------------------------------------------------------------
def _selftest() -> int:
    import numpy as np
    from qt_compat import QImage
    passed, failed = [], []

    def check(label, ok, extra=""):
        (passed if ok else failed).append(label)
        print("  [%s] %s %s" % ("OK" if ok else "FAIL", label,
                                extra if not ok else ""))

    # ① 提示词模板
    check("提示词覆盖白底/场景/礼盒/柔光/45度/佩戴/细节/详情三类",
          all(k in PROMPTS for k in ("白底", "场景", "礼盒", "柔光", "45度",
                                     "佩戴", "细节", "详情封面", "详情场景", "详情卖点")))
    p = prompt_for("白底", "项链")
    check("★ 白底提示词明确要「去掉手/皮肤」+「纯白」+「只保留产品」",
          "去掉托着它的手" in p and "纯白" in p and "只保留" in p, p[:80])
    check("细节提示词会替换 {part}", "爪镶" in prompt_for("细节", "项链", part="爪镶"))
    check("未知 kind 回退到场景模板", prompt_for("不存在", "项链") == prompt_for("场景", "项链"))

    # ② 去水印：合成一张白底 + 右下角灰字水印
    canvas = np.full((400, 400, 3), 255, dtype=np.uint8)
    canvas[150:260, 150:260] = (40, 60, 90)                   # 产品（深色块）
    for i in range(20):                                        # 右下角灰字水印
        canvas[370:378, 300 + i * 3: 300 + i * 3 + 2] = (200, 200, 200)
    cleared, cnt = strip_watermark(canvas)
    check("★ 右下角灰字水印被清除", cnt >= 1 and int(cleared[374, 305].min()) == 255,
          (cnt, tuple(int(v) for v in cleared[374, 305])))
    check("产品本体没被误伤", int(cleared[200, 200].min()) == 40,
          tuple(int(v) for v in cleared[200, 200]))
    # 反例：产品伸到右下角 → 不许抹
    c2 = np.full((400, 400, 3), 255, dtype=np.uint8)
    c2[300:400, 300:400] = (200, 200, 200)
    _c2, cnt2 = strip_watermark(c2, area_frac=0.01)
    check("★ 反例：大块灰（产品/阴影）不动它", cnt2 == 0, cnt2)
    # 反例：纯白图无异常
    _c3, cnt3 = strip_watermark(np.full((200, 200, 3), 255, dtype=np.uint8))
    check("反例：纯白图返回 0 处", cnt3 == 0)

    # ③ 方图合成
    img = np.full((500, 300, 3), 255, dtype=np.uint8)
    img[100:300, 50:250] = (30, 30, 30)
    sq = to_square_on_white(img, size=800)
    check("方图输出 800×800", sq.shape == (800, 800, 3), sq.shape)
    check("四角纯白", all(int(sq[y, x].min()) == 255
                          for y, x in ((3, 3), (3, 796), (796, 3), (796, 796))))
    bb = product_bbox(sq)
    check("产品居中（左右留白差 < 2%）",
          abs(((bb[0]) - (800 - bb[2] - 1)) / 800) < 0.02, bb)
    check("产品占画面 20~90%（不是贴边也不是一小点）",
          0.2 < float(nonwhite_mask(sq).mean()) < 0.9,
          round(float(nonwhite_mask(sq).mean()), 3))
    check("全白图合成要报错而不是给张白图",
          _raises(lambda: to_square_on_white(np.full((50, 50, 3), 255, dtype=np.uint8))))

    # ④ postprocess 端到端（假图，不联网）
    import tempfile
    tmpdir = tempfile.mkdtemp(prefix="imgai_")
    raw = os.path.join(tmpdir, "raw.png")
    _save_rgb(np.ascontiguousarray(np.dstack([
        np.full((600, 600), 255, dtype=np.uint8)] * 3)), raw)
    qi = QImage(raw)
    out_ok = os.path.join(tmpdir, "ok.png")
    info = postprocess(raw, out_ok, size=800)
    check("★ 空图（没产品）→ 如实判不合格，不硬造一张", not info.get("ok"), info.get("reason"))
    # 有产品的假图
    big = np.full((600, 600, 3), 255, dtype=np.uint8)
    big[200:400, 220:380] = (60, 60, 60)
    _save_rgb(big, raw)
    info2 = postprocess(raw, out_ok, size=800)
    check("★ 有产品的图 → 收拾成 800×800 白底并判合格", bool(info2.get("ok")), info2)
    check("coverage 被记下来（供 notes 用）", "coverage_after" in info2, info2)

    # ⑤ 引擎解析：空配置 → 不可用、不崩
    check("空 cfg → available() False（不炸）", available({}) is False)
    check("空 cfg → resolve_conf() None", resolve_conf({}) is None)
    check("★ 没引擎时 refine() 返回 None（回退实拍版，绝不假装出图）",
          refine({}, raw, os.path.join(tmpdir, "nope.png")) is None)
    check("src 不存在 → None", edit_product({"engines": {}}, os.path.join(tmpdir, "x.png"),
                                          os.path.join(tmpdir, "y.png")) is None)

    print("-" * 46)
    print("imgai 自检：%d 项，失败 %d" % (len(passed) + len(failed), len(failed)))
    for f in failed:
        print("   !! " + f)
    return 1 if failed else 0


def _raises(fn) -> bool:
    try:
        fn()
        return False
    except Exception:                                        # noqa: BLE001
        return True


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    print("用法: imgai.py --selftest")
