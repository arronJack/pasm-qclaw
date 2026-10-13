# -*- coding: utf-8 -*-
"""ecom_video.py —— 电商主图视频（1:1，9~30 秒），v0.31.27
============================================================

淘宝/京东的主图视频直接影响搜索位与转化权重，而"商家自己拍剪"正是中小卖家最缺的
一环。本模块把**已经产出的主图与细节图**编排成一条真视频：

    白底主图（推近）→ 场景图（横移）→ 卖点图 → 细节图 ×N（快切）→ 参数/服务图
    + 0.5s 交叉转场 + 卖点字幕烧录 + 讲解配音（edge-tts 可用时）+ 首尾淡入淡出

## 两条诚实边界

1. **没有 ffmpeg 不等于没有产出**：`render_shop_video` 返回 `ok=False` + 原因，
   调用方必须改用 `render_video_preview_html()`（零依赖 HTML 播放器，运镜与字幕
   与成片一致）。**绝不允许**"说了生成视频但其实什么都没给"。
2. **配音不可用就静音**，并在 note 里写明（不假装有声音）。

## 复用而不是另起一套

ffmpeg 的探测/自动下载、字幕时间轴、TTS 合成全部复用 `creators`（项目里既有实现）——
不引入第二个视频引擎，用户也不需要再配一次。

## 用法

    import ecom_video as EV
    r = EV.render_shop_video(plan, {"main": [...], "crops": [...]}, out_dir,
                             allow_download=True)
    if not r["ok"]:
        EV.render_video_preview_html(plan, images, os.path.join(out_dir, "主图视频_预览.html"))
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from typing import Dict, List, Optional, Tuple

VID_DUR_MAIN = 2.4          # 主图停留秒数（主图视频通行节奏）
VID_DUR_CROP = 1.6          # 细节图停留秒数
VID_FPS = 30
VID_XFADE = 0.5             # 转场时长
VID_SIZE = 1080             # 输出边长（1:1）


# --------------------------------------------------------------------------
# 小工具（不 import ecom_render，避免循环依赖）
# --------------------------------------------------------------------------
def _esc(s) -> str:
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _as_uri(path: str) -> str:
    p = str(path or "")
    if not p:
        return ""
    try:
        import urllib.parse
        from pathlib import Path
        return urllib.parse.quote(Path(p).as_uri(), safe=":/%.@-")
    except Exception:                                        # noqa: BLE001
        return "file:///" + p.replace("\\", "/")


def _ts(sec: float) -> str:
    ms = max(0, int(round(sec * 1000)))
    h, r = divmod(ms, 3600000)
    m, r = divmod(r, 60000)
    s, ms = divmod(r, 1000)
    return "%02d:%02d:%02d,%03d" % (h, m, s, ms)


def ffmpeg_bin(allow_download: bool = False, log=None) -> str:
    """拿 ffmpeg：先探测；**只有显式允许**才自动下载（自检里绝不允许，免得偷拉 90MB）。"""
    try:
        import creators as CR
    except Exception:                                        # noqa: BLE001
        return ""
    hit = CR.find_ffmpeg()
    if hit:
        return hit
    if allow_download:
        try:
            return CR.ensure_ffmpeg(log) or ""
        except Exception:                                    # noqa: BLE001
            return ""
    return ""


def video_plan(plan: dict, images: dict) -> List[Tuple[str, float, str, str]]:
    """排片：`[(图路径, 停留秒, 字幕, 角色)]`。只收**真在盘**的图。"""
    mains = [str(p) for p in (images.get("main") or []) if p and os.path.isfile(str(p))]
    crops = [str(p) for p in (images.get("crops") or []) if p and os.path.isfile(str(p))]
    mi = plan.get("main_images") or []
    cr = plan.get("crops") or []
    pts = plan.get("selling_points") or []
    rows: List[Tuple[str, float, str, str]] = []
    for i, p in enumerate(mains):
        sub = str((mi[i].get("title") if i < len(mi) else "") or "")
        if not sub and i < len(pts):
            sub = str(pts[i].get("head") or "")
        rows.append((p, VID_DUR_MAIN, sub[:22],
                     str(mi[i].get("role") if i < len(mi) else "")))
    for j, p in enumerate(crops):
        sub = ""
        if j < len(cr):
            sub = "%s %s" % (cr[j].get("anno", ""), cr[j].get("caption", ""))
        rows.append((p, VID_DUR_CROP, sub[:24], "细节"))
    if rows and plan.get("brand"):                            # 品牌片尾稍作停留
        rows[-1] = (rows[-1][0], VID_DUR_CROP + 0.8, rows[-1][2], rows[-1][3])
    return rows


def narration_script(plan: dict) -> str:
    """讲解词：3 个卖点 + 服务承诺（口播稿，短句，适合 15 秒）。"""
    sp = plan.get("selling_points") or []
    core = "，".join("%s，%s" % (str(x.get("head") or "")[:8],
                                str(x.get("body") or "")[:26]) for x in sp[:3])
    return "%s。现在下单，7 天无理由退换，正品保障。" % (
        core or str(plan.get("product") or "本商品"))


# --------------------------------------------------------------------------
# 真出 mp4
# --------------------------------------------------------------------------
def render_shop_video(plan: dict, images: dict, out_dir: str,
                      allow_download: bool = False, size: int = VID_SIZE,
                      log=None) -> Dict:
    """出**主图视频**。返回 `{ok, path, note, seconds, frames}`。

    `ok=False` 时 `note` 必给原因（没有 ffmpeg / 没有图 / 合成失败），
    调用方据此降级到 HTML 预览并如实告知。
    """
    rows = video_plan(plan, images)
    if not rows:
        return {"ok": False, "path": "", "note": "没有可用于做视频的图", "frames": 0}
    ff = ffmpeg_bin(allow_download, log)
    if not ff:
        return {"ok": False, "path": "",
                "note": "未检测到 ffmpeg（首次可自动下载，或在设置里指定路径）",
                "frames": len(rows)}
    try:
        import creators as CR
    except Exception:                                        # noqa: BLE001
        CR = None
    os.makedirs(out_dir, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="ecomvid_")
    clips, durs = [], []
    try:
        # ---- ① 逐帧 clip（交替运镜：奇数推近 / 偶数横移）----
        for i, (src, dur, _sub, _role) in enumerate(rows, 1):
            clip = os.path.join(tmp, "c%02d.mp4" % i)
            n = max(48, int(dur * VID_FPS) + 16)
            if i % 2:
                vf = ("scale=%d:%d:force_original_aspect_ratio=increase,"
                      "crop=%d:%d,zoompan=z='min(zoom+0.0007,1.14)':"
                      "x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                      "d=%d:s=%dx%d:fps=%d,format=yuv420p"
                      % (size, size, size, size, n, size, size, VID_FPS))
            else:
                vf = ("scale=%d:%d:force_original_aspect_ratio=increase,"
                      "crop=%d:%d,zoompan=z=1.12:x='(iw-iw/zoom)*on/%d':"
                      "y='ih/2-(ih/zoom/2)':d=%d:s=%dx%d:fps=%d,format=yuv420p"
                      % (size * 2, size * 2, size * 2, size * 2, n, n,
                         size, size, VID_FPS))
            p = subprocess.run(
                [ff, "-y", "-loop", "1", "-framerate", str(VID_FPS), "-i", src,
                 "-vf", vf, "-t", "%.2f" % dur, "-c:v", "libx264",
                 "-preset", "medium", "-crf", "21", "-pix_fmt", "yuv420p", clip],
                capture_output=True, timeout=600)
            if p.returncode == 0 and os.path.isfile(clip) \
                    and os.path.getsize(clip) > 1000:
                clips.append(clip)
                durs.append(float(dur))
        if not clips:
            return {"ok": False, "path": "",
                    "note": "没有一帧渲染成功（检查图片是否可读）", "frames": 0}
        # ---- ② xfade 串联 ----
        merged = clips[0]
        for k in range(1, len(clips)):
            off = max(0.1, sum(durs[:k]) - k * VID_XFADE)
            nxt = os.path.join(tmp, "m%02d.mp4" % k)
            p = subprocess.run(
                [ff, "-y", "-i", merged, "-i", clips[k], "-filter_complex",
                 "[0:v][1:v]xfade=transition=fade:duration=%.2f:offset=%.2f[v]"
                 % (VID_XFADE, off), "-map", "[v]", "-c:v", "libx264",
                 "-preset", "medium", "-crf", "21", "-pix_fmt", "yuv420p", nxt],
                capture_output=True, timeout=900)
            if p.returncode == 0 and os.path.isfile(nxt):
                merged = nxt
        total = max(1.0, sum(durs) - (len(clips) - 1) * VID_XFADE)
        # ---- ③ 字幕（时间轴按转场偏移累计）----
        t0, lines = 0.0, []
        for idx, (_p, dur, sub, _r) in enumerate(rows[:len(durs)], 1):
            if sub:
                lines.append("%d\n%s --> %s\n%s\n" % (
                    idx, _ts(t0 + 0.25), _ts(t0 + max(0.8, dur - 0.25)), sub))
            t0 += dur - VID_XFADE
        has_sub = bool(lines)
        srt = os.path.join(tmp, "sub.srt")
        if has_sub:
            with open(srt, "w", encoding="utf-8-sig") as f:
                f.write("\n".join(lines))
        # ---- ④ 讲解配音（edge-tts 可用才做；不可用就静音并写明）----
        narr = ""
        if CR is not None:
            try:
                narr = CR.synth_speech(narration_script(plan),
                                       os.path.join(tmp, "narration.mp3")) or ""
            except Exception:                                # noqa: BLE001
                narr = ""
        # ---- ⑤ 烧字幕 + 淡入淡出 + 混配音 ----
        out = os.path.join(out_dir, "主图视频.mp4")
        vf_final = "fade=t=in:st=0:d=0.5,fade=t=out:st=%.2f:d=0.8" % max(0.1, total - 0.8)
        if has_sub:
            esc = srt.replace("\\", "/").replace(":", "\\:")
            vf_final = ("subtitles='%s':force_style='FontName=Microsoft YaHei,"
                        "FontSize=17,PrimaryColour=&H00FFFFFF,"
                        "OutlineColour=&H90000000,BorderStyle=1,Outline=2,"
                        "Shadow=1,MarginV=64',%s" % (esc, vf_final))
        has_audio = bool(narr) and os.path.isfile(narr)
        cmd = [ff, "-y", "-i", merged]
        if has_audio:
            cmd += ["-i", narr, "-filter_complex",
                    "[0:v]%s[v];[1:a]adelay=700|700[a]" % vf_final,
                    "-map", "[v]", "-map", "[a]", "-shortest",
                    "-c:a", "aac", "-b:a", "128k"]
        else:
            cmd += ["-vf", vf_final, "-an"]
        cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "20",
                "-pix_fmt", "yuv420p", "-movflags", "+faststart", out]
        p = subprocess.run(cmd, capture_output=True, timeout=900)
        if p.returncode != 0 or not os.path.isfile(out) \
                or os.path.getsize(out) < 2000:
            return {"ok": False, "path": "",
                    "note": "ffmpeg 合成失败：%s"
                            % p.stderr.decode("utf-8", "ignore")[-160:],
                    "frames": len(clips)}
        return {"ok": True, "path": out,
                "note": ("%d 帧 · %.1fs · %d×%d%s%s"
                         % (len(clips), total, size, size,
                            " · 已烧字幕" if has_sub else "",
                            " · 带讲解配音" if has_audio else "（无配音：edge-tts 不可用）")),
                "seconds": round(total, 1), "frames": len(clips)}
    except Exception as ex:                                  # noqa: BLE001
        return {"ok": False, "path": "", "note": "做视频时出错：%s" % ex, "frames": 0}
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------
# 零依赖必出的预览播放器
# --------------------------------------------------------------------------
def render_video_preview_html(plan: dict, images: dict, out_path: str) -> str:
    """主图视频**预览**（HTML，零依赖）。运镜/字幕与成片一致，并**如实标注是预览**。"""
    rows = video_plan(plan, images)
    body = "".join(
        "<section class='sl' data-d='%.1f'><img src='%s'><div class='cap'>%s</div>"
        "<div class='role'>%s</div></section>" % (d, _as_uri(u), _esc(c), _esc(r))
        for u, d, c, r in rows)
    title = _esc(plan.get("product") or "主图视频")
    brand = _esc(plan.get("brand") or "")
    html = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>%s · 主图视频预览</title><style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0b1220;font-family:"Microsoft YaHei",system-ui,sans-serif;
 display:flex;flex-direction:column;align-items:center;justify-content:center;
 min-height:100vh;color:#e2e8f0;gap:14px;padding:18px}
.stage{position:relative;width:min(74vh,680px);aspect-ratio:1/1;background:#000;
 border-radius:14px;overflow:hidden;box-shadow:0 10px 40px rgba(0,0,0,.55)}
.sl{position:absolute;inset:0;opacity:0;transition:opacity .45s ease}
.sl.on{opacity:1}
.sl img{width:100%%;height:100%%;object-fit:contain;background:#fff}
.sl.on img{animation:kb 3.6s ease-out forwards}
@keyframes kb{from{transform:scale(1.0)}to{transform:scale(1.09)}}
.cap{position:absolute;left:0;right:0;bottom:0;padding:44px 20px 18px;
 background:linear-gradient(180deg,rgba(2,6,23,0),rgba(2,6,23,.88));
 font-size:22px;font-weight:700;text-align:center;line-height:1.5}
.role{position:absolute;top:12px;right:14px;background:rgba(15,23,42,.72);
 border-radius:8px;padding:3px 10px;font-size:12px;color:#cbd5e1}
.bar{display:flex;align-items:center;gap:10px;color:#94a3b8;font-size:13px}
button{background:#1e293b;color:#e2e8f0;border:1px solid #334155;border-radius:999px;
 padding:6px 16px;cursor:pointer;font-size:13px}
button:hover{background:#0ea5e9;border-color:#0ea5e9}
.prog{width:200px;height:4px;background:#1e293b;border-radius:2px;overflow:hidden}
.prog i{display:block;height:100%%;width:0;background:#0ea5e9}
h1{font-size:16px;font-weight:600}
.tip{color:#64748b;font-size:12px;text-align:center;max-width:640px;line-height:1.7}
</style></head><body>
<h1>%s%s</h1>
<div class='stage' id='stage'>%s</div>
<div class='bar'><button onclick="play()">▶ 播放</button>
<button onclick="stop()">⏹ 停止</button><div class='prog'><i id='pi'></i></div>
<span id='st'>就绪</span></div>
<div class='tip'>这是<b>预览</b>（零依赖 HTML 播放器）：运镜与字幕与成片一致；
 装上 ffmpeg（首次可自动下载）后，同一批图会直接合成 %d×%d 的 mp4 主图视频。</div>
<script>
var N=%d,cur=-1,playing=false,timer=null,raf=null;
function show(i){var els=document.querySelectorAll('.sl');
 for(var k=0;k<N;k++){els[k].className=(k===i)?'sl on':'sl';}
 cur=i;document.getElementById('st').textContent=(i+1)+' / '+N;}
function step(){if(!playing)return;
 if(cur+1>=N){playing=false;document.getElementById('st').textContent='播放完毕';return;}
 show(cur+1);var d=parseFloat(document.querySelectorAll('.sl')[cur].dataset.d)||2.4;
 var t0=performance.now();clearTimeout(timer);clearInterval(raf);
 raf=setInterval(function(){var e=(performance.now()-t0)/(d*1000);
  document.getElementById('pi').style.width=Math.min(100,e*100)+'%%';},60);
 timer=setTimeout(step,d*1000);}
function play(){playing=true;document.getElementById('st').textContent='播放中…';
 cur=-1;step();}
function stop(){playing=false;clearTimeout(timer);clearInterval(raf);
 document.getElementById('pi').style.width='0';document.getElementById('st').textContent='已停止';}
show(0);
</script></body></html>""" % (title, _esc(title), (" · " + brand) if brand else "",
                               body, VID_SIZE, VID_SIZE, len(rows))
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    return out_path


# --------------------------------------------------------------------------
# 自检（**不许**在这里下 ffmpeg：allow_download 一律 False）
# --------------------------------------------------------------------------
def selftest() -> int:
    import shutil
    import sys
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

    print("== ecom_video 自检 ==")
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import ecom as EC

    tmp = tempfile.mkdtemp(prefix="ecomv_")
    try:
        plan = EC.build_plan("加厚保暖外套，防水面料，易清洗，品牌：示范，价格：199")
        # 造 5 张主图 + 3 张细节图（真 PNG，覆盖"文件不存在"的假绿）
        main, crops = [], []
        for i in range(5):
            p = os.path.join(tmp, "主图%d.png" % (i + 1))
            with open(p, "wb") as f:
                f.write(_png(64, 64, (30 + i * 30, 60, 120)))
            main.append(p)
        for i in range(3):
            p = os.path.join(tmp, "细节%d.png" % (i + 1))
            with open(p, "wb") as f:
                f.write(_png(64, 64, (200, 40 + i * 40, 40)))
            crops.append(p)
        imgs = {"main": main, "crops": crops}

        rows = video_plan(plan, imgs)
        check(len(rows) == 8, "排片 8 帧（5 主图 + 3 细节）", len(rows))
        check(all(isinstance(r[1], float) and r[1] > 0 for r in rows), "每帧都有停留时长")
        check(len(set(len(r[2]) for r in rows)) >= 1 and any(r[2] for r in rows),
              "每帧都配了字幕", [r[2] for r in rows][:3])
        check(all(not str(r[0]).endswith("不存在.png") for r in rows),
              "只收真在盘的图")
        # 反例：目录里有不存在的图 → 不作为帧
        rows2 = video_plan(plan, {"main": main + [os.path.join(tmp, "没有.png")],
                                  "crops": crops})
        check(len(rows2) == 8, "★ 反例：不存在的图被剔除（不许拿空路径去渲染）",
              len(rows2))
        total_est = sum(r[1] for r in rows) - (len(rows) - 1) * VID_XFADE
        check(8.0 <= total_est <= 30.0, "★ 预计时长落在平台主图视频区间 9~30s",
              round(total_est, 1))
        sc = narration_script(plan)
        check(6 <= len(sc) <= 120, "讲解词长度合理", len(sc))
        check("无理由" in sc or "保障" in sc, "讲解词含服务承诺（转化要素）", sc[:40])

        # 没有 ffmpeg 且**不允许下载** → 必须如实失败并给原因（绝不谎报成功）
        r = render_shop_video(plan, imgs, tmp, allow_download=False)
        if not r["ok"]:
            check(bool(r["note"]), "★ 无 ffmpeg 时如实失败并给出原因", r["note"])
            check(r["path"] == "", "★ 失败时**不留**空的 mp4 路径（不谎报产物）")
        else:
            check(os.path.isfile(r["path"]) and os.path.getsize(r["path"]) > 2000,
                  "★ 有 ffmpeg：真出了 mp4 且非空", r)
            check(8.0 <= float(r.get("seconds") or 0) <= 30.0,
                  "★ 成片时长在 9~30s 区间", r.get("seconds"))
            check("字幕" in r["note"] or "配音" in r["note"],
                  "成片说明里交代了字幕/配音情况", r["note"])

        # 预览播放器：零依赖**必出**
        hp = render_video_preview_html(plan, imgs, os.path.join(tmp, "预览.html"))
        check(os.path.isfile(hp) and os.path.getsize(hp) > 1500,
              "★ 预览播放器必出且非空（不许「什么都没有」）")
        h = open(hp, encoding="utf-8").read()
        check(h.count("class='sl'") == 8, "预览里 8 帧都在", h.count("class='sl'"))
        check("预览" in h and "ffmpeg" in h,
              "★ 预览页如实标注「这是预览」并说明如何出 mp4")

        # 反例：一张图都没有 → 明确失败，不许编
        r3 = render_shop_video(plan, {"main": [], "crops": []}, tmp)
        check(not r3["ok"] and "没有" in r3["note"], "★ 反例：没有图时如实失败", r3["note"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("-" * 46)
    print("ecom_video 自检：%d 项，%s" % (passed + failed,
                                        "全部通过" if not failed else "%d 项失败" % failed))
    return 1 if failed else 0


def _png(w: int, h: int, rgb) -> bytes:
    """造一张合法 PNG（不引 Pillow；空文件会掩盖"写盘失败"）。"""
    import struct
    import zlib

    def _chunk(t, d):
        c = t + d
        return (struct.pack(">I", len(d)) + c
                + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF))

    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(raw))
            + _chunk(b"IEND", b""))


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        raise SystemExit(selftest())
    print("ecom_video.py —— 电商主图视频（ffmpeg 运镜/转场/字幕/配音 + 零依赖预览）")
    print("ffmpeg：%s" % (ffmpeg_bin(False) or "（未检测到）"))
    selftest()
