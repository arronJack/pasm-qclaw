"""creators —— PASM 创作引擎层（v0.17.0）。

工种「图像 / 视频 / 漫剧」真正的执行器，把过去的"只给提示词"升级成"真出成品"：

  · 引擎接入：配置集中放 cfg["engines"]（懒检测，首次使用弹出接入框，输入自动保存）。
    图像引擎支持两类：
      - OpenAI 兼容云服务（/v1/images/generations）—— 硅基流动 SiliconFlow 等；
      - 本地 Stable Diffusion WebUI（AUTOMATIC1111，/sdapi/v1/txt2img）—— 免 Key。
  · 逐镜配音：edge-tts 直接合成 mp3（不播放，供成片用）。
  · 成片渲染：
      - HTML 播放器：零外部依赖，永远可出（图像+字幕+配音，浏览器内放映）；
      - MP4 成片：检测到 ffmpeg 时额外合成真实 mp4（图文短片/漫剧样片）。

模块刻意不依赖 Qt / 不直接 import companion，便于离屏测试与两个 UI 复用。
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Dict, List, Optional

def _nowin_kw():
    """Windows 下让 ffmpeg/ffprobe 子进程不弹黑窗（v0.22.1 修复成片时弹窗）。"""
    return {"creationflags": 0x08000000} if os.name == "nt" else {}

# ---------------- 引擎元信息与默认值 ----------------

# 接入框的厂商预设：provider id -> (标签, 默认 base_url, 默认模型, 是否必填 Key)
PROVIDERS = [
    {"id": "siliconflow", "label": "硅基流动 SiliconFlow（OpenAI 兼容，推荐）",
     "base": "https://api.siliconflow.cn/v1",
     "model": "Kwai-Kolors/Kolors", "need_key": True,
     "hint": "到 https://cloud.siliconflow.cn 注册拿 Key。模型可选 "
             "Kwai-Kolors/Kolors（写实/插画均佳）、black-forest-labs/FLUX.1-schnell 等。"},
    {"id": "openai", "label": "OpenAI DALL·E",
     "base": "https://api.openai.com/v1",
     "model": "dall-e-3", "need_key": True,
     "hint": "需要 OpenAI 官方 Key（国内网络需自备代理）。"},
    {"id": "custom", "label": "自定义 OpenAI 兼容服务",
     "base": "", "model": "", "need_key": True,
     "hint": "任意提供 POST {base}/images/generations 的服务均可接入（如部分中转站、"
             "腾讯云/火山方舟的 OpenAI 兼容出口）。"},
    {"id": "sdwebui", "label": "本地 Stable Diffusion WebUI（免 Key）",
     "base": "http://127.0.0.1:7860", "model": "", "need_key": False,
     "hint": "本机已启动 AUTOMATIC1111 WebUI（设置里勾选 --api）后填地址即可，"
             "例如 http://127.0.0.1:7860"},
]

# 出图画幅预设（OpenAI 兼容服务普遍只接受形如 1024x1024 的字串；本地 SD 自动转宽高）
SIZE_PRESETS = [
    ("1:1 方图 1024x1024", "1024x1024"),
    ("3:4 竖图 896x1152", "896x1152"),
    ("16:9 横图 1216x704", "1216x704"),
]

DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"   # 漫剧/视频解说默认女声
_NARR_SEC_PER_CHAR = 4.8                 # 中文字符/秒 的语速估计（无 ffprobe 时用）


# ---------------- 引擎配置读写（存于 cfg["engines"]） ----------------

def engines(cfg: dict) -> dict:
    """cfg 里的引擎区（惰性初始化，不落盘，由调用方统一 _save_json）。"""
    if not isinstance(cfg.get("engines"), dict):
        cfg["engines"] = {}
    return cfg["engines"]


def get_engine(cfg: dict, kind: str = "image") -> Optional[dict]:
    """读取某工种引擎配置（图像引擎被视频/漫剧复用）。"""
    en = engines(cfg)
    if kind != "image" and kind not in en:      # video/manga 共享图像引擎
        kind = "image"
    conf = en.get(kind)
    return conf if isinstance(conf, dict) and conf.get("base_url") else None


def conf_ok(conf: Optional[dict]) -> bool:
    """配置是否足以发起出图。"""
    if not conf or not str(conf.get("base_url") or "").strip():
        return False
    pid = conf.get("provider") or ""
    if pid == "sdwebui":
        return True
    return bool(str(conf.get("api_key") or "").strip() and
                str(conf.get("model") or "").strip())


def apply_preset(conf: dict, pid: str) -> dict:
    """按厂商预设填充字段（保留用户已填的 Key）。"""
    for p in PROVIDERS:
        if p["id"] == pid:
            conf["provider"] = pid
            if not conf.get("base_url"):
                conf["base_url"] = p["base"]
            if not conf.get("model") and p["model"]:
                conf["model"] = p["model"]
            if not conf.get("size"):
                conf["size"] = "1024x1024"
            return conf
    return conf


def probe(conf: dict, timeout: float = 6.0):
    """连通性/凭据探测。返回 (ok: bool, msg: str)。不消耗出图额度：
    云端走 GET {base}/models（OpenAI 兼容服务普遍提供），本地 SD 走根路径握手。"""
    base = str(conf.get("base_url") or "").rstrip("/")
    if not base:
        return False, "请先填写服务地址（Base URL）。"
    try:
        if conf.get("provider") == "sdwebui":
            url = base + "/"
        else:
            url = base + "/models"
        req = urllib.request.Request(url, headers={
            "Authorization": "Bearer " + str(conf.get("api_key") or ""),
            "User-Agent": "PASMStudio/0.17"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read(5000).decode("utf-8", "ignore")
        if conf.get("provider") == "sdwebui":
            return True, "本地绘图服务连接正常。"
        try:
            data = json.loads(body)
            ids = [m.get("id", "") for m in data.get("data", [])][:6]
        except Exception:
            ids = []
        if ids:
            return True, ("服务连接正常，可用模型示例：" + "、".join(ids) +
                          ("…" if len(ids) >= 6 else ""))
        return True, "服务连接正常（未列出模型，保存后直接试出图即可）。"
    except urllib.error.HTTPError as ex:
        if ex.code in (401, 403):
            return False, "鉴权失败（HTTP %d）：API Key 无效或没有权限。" % ex.code
        return False, "服务返回 HTTP %d，请检查地址与配置。" % ex.code
    except Exception as ex:
        return False, "连不上：%s" % ex


def _http_json(url: str, payload: Optional[dict] = None, api_key: str = "",
               timeout: float = 180.0, method: str = "POST") -> dict:
    """统一发 JSON 请求；返回解析后的 dict（错误抛 RuntimeError 带可读信息）。"""
    headers = {"User-Agent": "PASMStudio/0.17", "Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
    except urllib.error.HTTPError as ex:
        detail = ""
        try:
            detail = ex.read(600).decode("utf-8", "ignore")
        except Exception:
            pass
        raise RuntimeError("服务返回 HTTP %d：%s" % (ex.code, detail[:300]))
    except Exception as ex:
        raise RuntimeError("网络请求失败：%s" % ex)
    try:
        return json.loads(raw.decode("utf-8", "ignore"))
    except Exception:
        raise RuntimeError("返回内容不是 JSON（可能是网关错误页）。")


def _save_b64_or_url(item: dict, out_path: str):
    """把 /images/generations 返回项（b64_json 或 url）落成图片文件。"""
    if item.get("b64_json"):
        img = base64.b64decode(item["b64_json"])
        with open(out_path, "wb") as f:
            f.write(img)
        return
    url = item.get("url")
    if url:
        req = urllib.request.Request(url, headers={"User-Agent": "PASMStudio/0.17"})
        with urllib.request.urlopen(req, timeout=180) as r, open(out_path, "wb") as f:
            f.write(r.read())
        return
    raise RuntimeError("返回里既没有图也没有下载地址。")


def generate_image(conf: dict, prompt: str, out_path: str,
                   size: str = "1024x1024",
                   init_image: Optional[str] = None,
                   strength: float = 0.62) -> str:
    """按配置出一张图存到 out_path，返回路径。失败抛 RuntimeError（中文可读）。

    init_image 非空时：本地 Stable Diffusion WebUI 走真实 img2img（基于该图编辑）；
    strength 为重绘幅度：0.3~0.4=轻微调整（亮度/色调/柔光，构图基本不动），
    0.6+=大改（换风格/改主体动作）。云端 OpenAI 兼容引擎普遍没有图生图接口，
    会忽略 init 按提示词重画 —— 调用方应先用 `supports_img2img(conf)` 判断，
    云端改为"延续风格续画"并如实说明。
    """
    base = str(conf.get("base_url") or "").rstrip("/")
    api_key = str(conf.get("api_key") or "")
    model = str(conf.get("model") or "").strip()
    prompt = (prompt or "").strip()
    if not prompt:
        raise RuntimeError("出图提示词为空。")
    if conf.get("provider") == "sdwebui":
        w, h = _parse_size(size, 1024, 1024)
        payload = {"prompt": prompt, "negative_prompt": "text, watermark, blurry, lowres",
                   "steps": 28, "width": w, "height": h, "cfg_scale": 7,
                   "sampler_name": "DPM++ 2M Karras", "batch_size": 1}
        if init_image:
            # 真实图生图：上一张图作为底图，按修改提示词重绘
            # denoising_strength 由调用方按"轻改/大改"分级（v0.18.0）
            _den = max(0.25, min(0.9, float(strength or 0.62)))
            try:
                with open(init_image, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode("ascii")
            except Exception as ex:
                raise RuntimeError("读取上一张图失败：" + str(ex))
            payload = {"init_images": [b64], "prompt": prompt,
                       "negative_prompt": "text, watermark, blurry, lowres",
                       "steps": 28, "width": w, "height": h, "cfg_scale": 7,
                       "denoising_strength": _den,
                       "sampler_name": "DPM++ 2M Karras", "batch_size": 1}
            try:
                out = _http_json(base + "/sdapi/v1/img2img", payload, "", timeout=300)
            except RuntimeError as ex:
                # 个别 WebUI 版本参数不兼容 → 精简重试
                if "400" in str(ex) or "sampler" in str(ex).lower():
                    out = _http_json(base + "/sdapi/v1/img2img",
                                     {"init_images": [b64], "prompt": prompt,
                                      "steps": 24, "width": w, "height": h,
                                      "denoising_strength": _den, "batch_size": 1},
                                     "", timeout=300)
                else:
                    raise
        else:
            try:
                out = _http_json(base + "/sdapi/v1/txt2img", payload, "", timeout=300)
            except RuntimeError as ex:
                if "sampler" in str(ex).lower() or "400" in str(ex):
                    out = _http_json(base + "/sdapi/v1/txt2img",
                                     {"prompt": prompt, "steps": 24, "width": w, "height": h,
                                      "batch_size": 1}, "", timeout=300)
                else:
                    raise
        imgs = out.get("images") or []
        if not imgs:
            raise RuntimeError("本地绘图没返回图片（检查 WebUI 日志）。")
        with open(out_path, "wb") as f:
            f.write(base64.b64decode(imgs[0]))
        return out_path
    # ---- OpenAI 兼容云服务 ----
    if not model:
        raise RuntimeError("还没填模型名（Model）。")
    payload = {"model": model, "prompt": prompt, "size": size, "n": 1}
    try:
        out = _http_json(base + "/images/generations", payload, api_key)
    except RuntimeError as ex:
        # 个别服务不接受自定义 size → 去掉 size 重试一次
        if "size" in str(ex).lower() or "400" in str(ex):
            out = _http_json(base + "/images/generations",
                             {"model": model, "prompt": prompt, "n": 1}, api_key)
        else:
            raise
    items = out.get("data") or []
    if not items:
        err = out.get("error") or out.get("message") or ""
        raise RuntimeError("出图服务没返回结果。%s" % (str(err)[:200] if err else ""))
    _save_b64_or_url(items[0], out_path)
    return out_path


def supports_img2img(conf: dict) -> bool:
    """当前图像引擎是否支持真实图生图（只有本地 SD WebUI 支持）。"""
    return bool(conf and conf.get("provider") == "sdwebui")


def _parse_size(size: str, dw: int = 1024, dh: int = 1024) -> tuple:
    m = re.match(r"(\d+)[xX×](\d+)", str(size or ""))
    if m:
        return int(m.group(1)), int(m.group(2))
    return dw, dh


# ---------------- 配音（edge-tts → mp3 文件） ----------------

def synth_speech(text: str, out_mp3: str,
                 voice: str = DEFAULT_VOICE,
                 log: Optional[Callable[[str], None]] = None) -> Optional[str]:
    """把一句台词合成 mp3（直接落文件，不播放）。成功返回路径，失败返回 None。
    自动多音色兜底：主声线失败换男女声各一档再试。"""
    text = (text or "").strip()
    if not text:
        return None
    voices = [voice, DEFAULT_VOICE, "zh-CN-YunxiNeural", "zh-CN-XiaoyiNeural"]
    voices = list(dict.fromkeys(v for v in voices if v))
    try:
        import edge_tts  # 延迟导入：无该库时只影响成片配音，不影响出图
    except Exception:
        if log:
            log("配音库不可用（edge-tts 缺失），跳过配音。")
        return None
    for v in voices:
        try:
            async def _one():
                com = edge_tts.Communicate(text, v)
                await com.save(out_mp3)
            asyncio.run(_one())
            if os.path.exists(out_mp3) and os.path.getsize(out_mp3) > 512:
                return out_mp3
        except Exception as ex:
            if log:
                log("配音 %s 失败：%s" % (v, ex))
    return None


# ---------------- 分镜/剧本文本 → 结构化故事 ----------------

_SCENE_HINTS = ("旁白/解说一句（10~40字，中文口语，和画面对应）",
                "画面内容（用画面语言描述主体、动作、环境、景别）",
                "出图用英文 prompt（含统一风格词，只写 prompt 本身）")


def normalize_story(text: str, kind: str = "manga", max_scenes: int = 6) -> Optional[dict]:
    """把模型输出的故事企划转成结构化 dict：
    {title, style, scenes:[{idx, narration, subtitle, visual, prompt, prompt_en}]}
    支持 JSON 输出与宽松 Markdown 输出；解析失败返回 None（由调用方降级成纯脚本）。"""
    raw = (text or "").strip()
    if not raw:
        return None
    # 1) 优先 JSON（允许被 ```json 包裹；也处理开头是解释文字的情况）
    obj = _extract_json(raw)
    if isinstance(obj, dict):
        story = _json_to_story(obj, kind, max_scenes)
        if story and story.get("scenes"):
            return story
    # 2) 宽松模式：按 ## 场景/【N】/SCENE 行切分
    return _loose_to_story(raw, kind, max_scenes)


def _extract_json(text: str):
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    cand = m.group(1) if m else None
    if not cand:
        s = text.find("{")
        e = text.rfind("}")
        cand = text[s:e + 1] if 0 <= s < e else ""
    for c in (cand,):
        try:
            return json.loads(c)
        except Exception:
            pass
    # 常见小毛病的轻修：中文引号/尾逗号
    fix = cand.replace("，", ",").replace("：", ":")
    fix = re.sub(r",\s*([}\]])", r"\1", fix)
    try:
        return json.loads(fix)
    except Exception:
        return None


def _json_to_story(obj: dict, kind: str, max_scenes: int) -> Optional[dict]:
    scenes = obj.get("scenes") or obj.get("episodes") or obj.get("shots") or []
    if isinstance(obj.get("episode"), dict):      # 单集包裹
        scenes = obj["episode"].get("scenes") or scenes
    if not isinstance(scenes, list) or not scenes:
        return None
    out_scenes: List[dict] = []
    for i, sc in enumerate(scenes[:max_scenes]):
        if isinstance(sc, str):
            sc = {"narration": sc}
        if not isinstance(sc, dict):
            continue
        narr = str(sc.get("narration") or sc.get("narrator") or sc.get("旁白")
                   or sc.get("subtitle") or sc.get("台词") or "").strip()
        sub = str(sc.get("subtitle") or sc.get("字幕") or narr).strip()
        vis = str(sc.get("visual") or sc.get("画面") or sc.get("visual_cn")
                  or "").strip()
        pen = str(sc.get("prompt") or sc.get("prompt_en") or sc.get("出图提示词")
                  or vis).strip()
        if not (vis or pen or narr):
            continue
        out_scenes.append({"idx": i + 1, "narration": narr, "subtitle": sub or narr,
                           "visual": vis, "prompt": pen})
    if not out_scenes:
        return None
    style = str(obj.get("style") or obj.get("画风") or "").strip()
    title = str(obj.get("title") or obj.get("片名") or obj.get("name")
                or ("漫剧样片" if kind == "manga" else "图文短片")).strip()
    return {"title": title, "style": style, "kind": kind, "scenes": out_scenes}


def _loose_to_story(text: str, kind: str, max_scenes: int) -> Optional[dict]:
    """没有 JSON 时的兜底：按行分镜（## 镜号/字幕行）尽量还原。"""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    title = "漫剧样片" if kind == "manga" else "图文短片"
    scenes: List[dict] = []
    cur: Optional[dict] = None
    for ln in lines:
        if re.match(r"^(#+\s*)?(镜|镜头|scene|SCENE|幕|第?\d+)\s*[#0-9：:\.\-、]", ln) or \
           re.match(r"^#{1,3}\s", ln):
            if cur and (cur.get("visual") or cur.get("narration")):
                scenes.append(cur)
            cur = {"idx": len(scenes) + 1, "narration": "", "subtitle": "",
                   "visual": "", "prompt": ""}
            if len(scenes) >= max_scenes:
                break
            continue
        if not cur:
            cur = {"idx": 1, "narration": "", "subtitle": "", "visual": "", "prompt": ""}
        if not cur["visual"] and not cur["narration"] and not cur["prompt"]:
            cur["narration"] = cur["subtitle"] = ln
        elif ln.startswith(("画面", "visual", "Visual", "图", "场景")):
            cur["visual"] = ln.split("：", 1)[-1].split(":", 1)[-1]
        elif ln.startswith(("prompt", "Prompt", "提示词", "出图")):
            cur["prompt"] = re.sub(r"^(prompt|Prompt|提示词|出图提示词)[：:\s]*", "", ln)
        else:
            if not cur["visual"]:
                cur["visual"] = ln
                cur["prompt"] = cur["prompt"] or ln
            elif not cur.get("narration") and len(ln) < 80:
                cur["subtitle"] = cur["narration"] = ln
    if cur and (cur.get("visual") or cur.get("narration")):
        scenes.append(cur)
    scenes = scenes[:max_scenes]
    if not scenes:
        return None
    return {"title": title, "style": "", "kind": kind, "scenes": scenes}


def story_text(story: dict) -> str:
    """给聊天用的分镜概览文本。"""
    title = story.get("title", "")
    kind = story.get("kind", "manga")
    head = f"📖 片名：{title}（{'漫剧' if kind == 'manga' else '图文短片'} · 共 {len(story['scenes'])} 镜）"
    if story.get("style"):
        head += f"\n🎨 画风：{story['style']}"
    rows = [head]
    for sc in story["scenes"]:
        row = f"[第{sc['idx']}镜] " + (sc.get("subtitle") or sc.get("narration") or "")[:40]
        rows.append(row)
    return "\n".join(rows)


# ---------------- 成片渲染 ----------------

def ffmpeg_bin_dir() -> str:
    """PASM Studio 自带/自动下载的 ffmpeg 落点（用户目录，免管理员权限）。"""
    if os.name == "nt":
        return os.path.join(os.environ.get("LOCALAPPDATA") or
                            os.path.expanduser("~"), "PASMStudio", "bin")
    return os.path.join(os.path.expanduser("~"), ".pasmstudio", "bin")


def find_ffmpeg() -> str:
    """探测系统 ffmpeg（含常见安装位置与本应用自动下载位置），找不到返回空串。"""
    cached = getattr(find_ffmpeg, "_cached", None)
    if cached is not None:
        return cached
    hit = shutil.which("ffmpeg") or ""
    if not hit:
        for p in (os.path.join(ffmpeg_bin_dir(), "ffmpeg.exe"),
                  os.path.join(ffmpeg_bin_dir(), "ffmpeg"),
                  r"C:\ffmpeg\bin\ffmpeg.exe",
                  os.path.expandvars(r"%LOCALAPPDATA%\ffmpeg\bin\ffmpeg.exe"),
                  r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
                  "/usr/bin/ffmpeg", "/usr/local/bin/ffmpeg"):
            if os.path.isfile(p):
                hit = p
                break
    find_ffmpeg._cached = hit
    return hit


_FFMPEG_SOURCES = (
    # npmmirror（淘宝 CDN，国内最快）：ffmpeg-static 单文件 gzip 构建（ffmpeg 6.0）
    ("https://registry.npmmirror.com/-/binary/ffmpeg-static/b6.0/"
     "ffmpeg-win32-x64.gz", "gz"),
    # gyan.dev essentials：体积小(~90MB)够用（libx264/aac/libass 全带）
    ("https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip", "zip"),
    # GitHub 镜像兜底
    ("https://github.com/GyanD/ffmpeg-builds/releases/latest/download/"
     "ffmpeg-latest-win64-essentials.zip", "zip"),
)


def ensure_ffmpeg(progress: Optional[Callable[[str], None]] = None) -> str:
    """确保 ffmpeg 可用：探测失败时自动下载静态构建并解压到应用 bin 目录。

    progress(msg) 用于 UI 提示；成功返回 ffmpeg 路径，失败抛 RuntimeError。
    只下载主程序 ffmpeg.exe（ffprobe 非必需：时长探测走 ffmpeg -i 解析）。
    """
    hit = find_ffmpeg()
    if hit:
        return hit
    if os.name != "nt":
        raise RuntimeError("未检测到 ffmpeg，请先安装（apt/brew install ffmpeg）")
    say = progress or (lambda m: None)

    def _fetch(url: str, dst: str) -> str:
        req = urllib.request.Request(url, headers={"User-Agent": "PASMStudio"})
        with urllib.request.urlopen(req, timeout=60) as resp, open(dst, "wb") as f:
            total = int(resp.headers.get("Content-Length") or 0)
            got = 0
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
                if total:
                    say(f"⬇ 下载 ffmpeg… {got // (1 << 20)}/{total // (1 << 20)} MB")
        return dst

    import zipfile
    bin_dir = ffmpeg_bin_dir()
    os.makedirs(bin_dir, exist_ok=True)
    last_err = ""
    for url, kind in _FFMPEG_SOURCES:
        suffix = ".zip" if kind == "zip" else ".gz"
        tmp_dl = os.path.join(bin_dir, "_ffmpeg_dl" + suffix)
        try:
            say("⬇ 首次使用视频合成，正在自动下载 ffmpeg（约 80~90MB，一次性）…")
            _fetch(url, tmp_dl)
            ff_path = os.path.join(bin_dir, "ffmpeg.exe")
            if kind == "gz":
                # ffmpeg-static 单文件 gzip：直接 gunzip 成 ffmpeg.exe
                import gzip
                say("📦 解压 ffmpeg…")
                with gzip.open(tmp_dl, "rb") as src, open(ff_path, "wb") as out:
                    while True:
                        chunk = src.read(1 << 20)
                        if not chunk:
                            break
                        out.write(chunk)
            else:
                say("📦 解压 ffmpeg…")
                with zipfile.ZipFile(tmp_dl) as z:
                    for name in z.namelist():
                        if os.path.basename(name).lower() == "ffmpeg.exe":
                            with z.open(name) as src, open(ff_path, "wb") as out:
                                out.write(src.read())
                            break
            for f in (tmp_dl,):
                try:
                    os.remove(f)
                except OSError:
                    pass
            if os.path.isfile(ff_path) and os.path.getsize(ff_path) > 10_000_000:
                find_ffmpeg._cached = ff_path
                say("✅ ffmpeg 就绪")
                return ff_path
            last_err = "解压结果异常"
        except Exception as ex:
            last_err = str(ex)
    raise RuntimeError("ffmpeg 自动下载失败：" + last_err +
                       "。可手动安装 ffmpeg 后重试。")


def render_html_player(pkg_dir: str, story: dict, frames: List[dict]) -> str:
    """生成可放映的 HTML 播放器（必出）。frames 每项含 file(图片绝对路径)、
    audio(配音 mp3 或空)、narration/subtitle。返回 index.html 路径。"""
    title = story.get("title") or "未命名"
    style = story.get("style") or ""
    slides = []
    for i, fr in enumerate(frames, 1):
        img = _as_uri(fr.get("file") or "")
        aud = _as_uri(fr.get("audio") or "") if fr.get("audio") else ""
        sub = (fr.get("subtitle") or fr.get("narration") or f"第 {i} 镜").strip()
        slides.append((img, aud, sub))
    css_zoom = (
        "@keyframes kb_odd{0%{transform:scale(1.03) translate(0,0);}"
        "100%{transform:scale(1.22) translate(1.6%,-1.2%)}}"
        "@keyframes kb_even{0%{transform:scale(1.24) translate(-1.6%,1%)}"
        "100%{transform:scale(1.05) translate(0,0)}}"
        "@keyframes fadein{from{opacity:0}to{opacity:1}}"
        "@keyframes subUp{from{opacity:0;transform:translate(-50%,14px)}"
        "to{opacity:1;transform:translate(-50%,0)}}")
    slides_html = []
    for i, (img, aud, sub) in enumerate(slides, 1):
        aud_block = (f"<audio id='a{i}' src='{aud}' preload='auto'></audio>" if aud else "")
        slides_html.append(
            f"""<section class='slide' id='s{i}' data-a='{i if aud else 0}'>
              <div class='imgbox'><img src='{img}' alt='第{i}镜'></div>
              <div class='sub'>{_esc(sub)}</div>{aud_block}
            </section>""")
    html = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>{_esc(title)}</title>
<style>
{css_zoom}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:#0b1220;color:#e2e8f0;font-family:"Microsoft YaHei",system-ui,sans-serif;
  display:flex;flex-direction:column;height:100vh;overflow:hidden}}
h1{{font-size:18px;font-weight:700;text-align:center;padding:12px 8px 2px}}
.tip{{text-align:center;color:#94a3b8;font-size:12px;padding-bottom:6px}}
.stage{{flex:1;position:relative;margin:0 12px;background:#000;border-radius:14px;
  overflow:hidden;box-shadow:0 8px 30px rgba(0,0,0,.5)}}
.slide{{position:absolute;inset:0;display:none}}
.slide.on{{display:block;animation:fadein .55s ease both}}
.imgbox{{position:absolute;inset:0;overflow:hidden;background:#000}}
.imgbox img{{width:100%;height:100%;object-fit:contain}}
.slide.on .imgbox img{{animation:kb_odd 9.5s ease-in-out forwards}}
.slide:nth-child(even).on .imgbox img{{animation-name:kb_even}}
.sub{{position:absolute;left:50%;bottom:26px;transform:translateX(-50%);
  background:linear-gradient(180deg,rgba(2,6,23,0),rgba(2,6,23,.82) 30%);
  color:#fff;padding:14px 20px 10px;border-radius:14px;font-size:17px;
  letter-spacing:1px;max-width:88%;text-align:center;line-height:1.6;
  text-shadow:0 1px 3px rgba(0,0,0,.9);border:1px solid rgba(148,163,184,.22)}}
.slide.on .sub{{animation:subUp .7s .3s ease both}}
.bar{{display:flex;align-items:center;justify-content:center;gap:10px;padding:12px}}
.bar button{{border:1px solid #334155;background:#1e293b;color:#e2e8f0;border-radius:999px;
  padding:6px 16px;cursor:pointer;font-size:13px}}
.bar button:hover{{background:#0ea5e9;border-color:#0ea5e9}}
.pos{{color:#94a3b8;font-size:12px;min-width:60px;text-align:center}}
.state{{color:#38bdf8;font-size:12px;min-width:130px;text-align:center}}
</style></head><body>
<h1>{_esc(title)}</h1>
<div class='tip'>{_esc('PASM 漫剧/短片播放器 · ' + (style or '图文短片') +
  ' · 空格/→ 下一镜 · ← 上一镜 · B 全部连放 · 点画面也翻镜')}</div>
<div class='stage' id='stage'>{''.join(slides_html)}</div>
<div class='bar'>
  <button onclick="nav(-1)">‹ 上一镜</button>
  <span class='pos' id='pos'>1 / {len(slides)}</span>
  <button onclick="nav(1)">下一镜 ›</button>
  <button onclick="playAll()">▶ 全部连放</button>
  <button onclick="stopAll()">⏹ 停止</button>
  <span class='state' id='state'></span>
</div>
<script>
var N={len(slides)},cur=0,seq=null,playing=false;
function show(i){{
  if(i<0||i>=N)return;cur=i;
  for(var j=1;j<=N;j++){{
    var s=document.getElementById('s'+j);
    if(j===cur+1){{s.className='slide on';restartAnim(s);}}
    else s.className='slide';
  }}
  if(seq){{clearTimeout(seq);seq=null;}}
  var el=document.getElementById('s'+(cur+1)),a=el.getAttribute('data-a');
  var st=document.getElementById('state');
  if(a&&a!=='0'){{
    var au=document.getElementById('a'+a);au.currentTime=0;
    st.textContent='🔊 配音中…';
    au.onended=function(){{st.textContent='';if(playing)seq=setTimeout(nextStep,500)}};
    au.play().catch(function(){{st.textContent='';}});
  }} else st.textContent='';
  document.getElementById('pos').textContent=(cur+1)+' / '+N;
}}
function restartAnim(el){{
  var im=el.querySelector('img');if(!im)return;
  im.style.animation='none';void im.offsetWidth;im.style.animation='';
}}
function nextStep(){{if(cur+1>=N){{playing=false;document.getElementById('state').textContent='播放完毕';return}}show(cur+1)}}
function nav(d){{playing=false;if(seq)clearTimeout(seq);show(cur+d)}}
function playAll(){{
  playing=true;if(seq)clearTimeout(seq);
  show(0);
  function step(){{
    if(!playing)return;
    var el=document.getElementById('s'+(cur+1)),a=el.getAttribute('data-a');
    if(a&&a!=='0')return;            /* 有配音：等 ended 事件驱动 */
    seq=setTimeout(step,3800);
  }}
  seq=setTimeout(step,3800);
}}
function stopAll(){{playing=false;if(seq)clearTimeout(seq);seq=null;
  for(var k=1;k<=N;k++){{var a=document.getElementById('s'+k).getAttribute('data-a');
    if(a&&a!=='0'){{var au=document.getElementById('a'+a);au.pause();au.currentTime=0}}}}
  document.getElementById('state').textContent='已停止';}}
document.getElementById('stage').addEventListener('click',function(e){{
  if(e.target.tagName!=='A')nav(1);}});
document.addEventListener('keydown',function(e){{
  if(e.key===' '){{e.preventDefault();nav(1)}}
  else if(e.key==='ArrowRight')nav(1);
  else if(e.key==='ArrowLeft')nav(-1);
  else if(e.key==='b'||e.key==='B')playAll();}});
show(0);
</script></body></html>"""
    path = os.path.join(pkg_dir, "播放器.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path


def render_mp4(pkg_dir: str, frames: List[dict], ffmpeg: str = "",
               title: str = "") -> str:
    """ffmpeg 合图成真视频成片（v0.20.0 专业管线）：

    每镜 = 图片 Ken Burns 运镜（zoompan）+ 配音对齐；镜间 **xfade 叠化转场**
    （fade/circleopen/smoothleft 交替）；成片烧录 **srt 字幕**（旁白/台词）；
    首尾 **淡入淡出**。返回 mp4 路径；失败抛异常（调用方降级保留 HTML 播放器）。
    """
    if not ffmpeg:
        ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("未检测到 ffmpeg")
    clips: List[str] = []
    tmp = tempfile.mkdtemp(prefix="pasm_clip_")
    W, H, FPS, XFD = 1280, 720, 25, 0.8
    try:
        for i, fr in enumerate(frames, 1):
            src = fr.get("file")
            aud = fr.get("audio")
            if not src or not os.path.isfile(src):
                continue
            clip = os.path.join(tmp, "c%02d.mp4" % i)
            ext = os.path.splitext(src)[1].lower()
            if ext in (".mp4", ".webm", ".mov", ".mkv", ".avi"):
                # ---- v0.21.0 真视频片段（图生视频产出）：动画已内置，只做规格统一 ----
                has_a = _probe_audio(src, ffmpeg)
                has_n = bool(aud) and os.path.isfile(aud)
                # 显式限长：anullsrc/apad 都是无限源，-shortest 在
                # filter_complex 下不可靠（实测挂死），必须 -t 截断
                dur = max(1.0, _probe_dur(src, ffmpeg))
                cmd = [ffmpeg, "-y", "-i", src]
                fc = (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,"
                      f"crop={W}:{H},fps={FPS},format=yuv420p[v]")
                if has_a and has_n:
                    # 片段原声（模型同步生成）+ 旁白混音，以画面时长为准
                    fc += (";[0:a]aresample=44100[a0];"
                           "[1:a]aresample=44100,apad[a1];"
                           "[a0][a1]amix=inputs=2:duration=first"
                           ":dropout_transition=0[a]")
                    cmd += ["-i", aud]
                elif has_a:
                    fc += ";[0:a]aresample=44100[a]"
                elif has_n:
                    fc += ";[1:a]aresample=44100,apad[a]"
                    cmd += ["-i", aud]
                else:
                    fc += (";anullsrc=channel_layout=stereo"
                           ":sample_rate=44100[a]")
                    cmd += ["-f", "lavfi", "-i",
                            "anullsrc=channel_layout=stereo:sample_rate=44100"]
                cmd += ["-filter_complex", fc, "-map", "[v]", "-map", "[a]",
                        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
                        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
                        "-t", "%.2f" % dur, clip]
                _run_ff(cmd)
            else:
                # ---- 静态图：Ken Burns 运镜（无引擎零成本兜底）----
                dur = _audio_dur(aud, ffmpeg) if aud else 4.0
                dur = max(2.0, dur + 0.5)
                frames_n = max(50, int(dur * FPS) + 15)
                # 相邻镜交替运镜方向：偶数镜轻微横移，奇数镜居中推近，避免呆板
                if i % 2 == 0:
                    vf = (f"scale={W}:{H}:force_original_aspect_ratio=increase,"
                          f"crop={W}:{H},zoompan=z='min(1.10+0.0008*on,1.22)':"
                          f"x='(iw-iw/zoom)/3':y='ih/2-(ih/zoom/2)':d={frames_n}:"
                          f"s={W}x{H}:fps={FPS},format=yuv420p")
                else:
                    vf = (f"scale={W}:{H}:force_original_aspect_ratio=increase,"
                          f"crop={W}:{H},zoompan=z='min(zoom+0.0006,1.15)':"
                          f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames_n}:"
                          f"s={W}x{H}:fps={FPS},format=yuv420p")
                cmd = [ffmpeg, "-y", "-loop", "1", "-framerate", str(FPS),
                       "-i", src]
                if aud and os.path.isfile(aud):
                    cmd += ["-i", aud]
                cmd += ["-vf", vf, "-c:v", "libx264", "-preset", "medium",
                        "-crf", "22", "-pix_fmt", "yuv420p"]
                if aud and os.path.isfile(aud):
                    cmd += ["-c:a", "aac", "-b:a", "128k", "-ar", "44100"]
                cmd += ["-t", "%.2f" % dur, "-shortest", clip]
                _run_ff(cmd)
            if os.path.isfile(clip) and os.path.getsize(clip) > 1000:
                clips.append(clip)
        if not clips:
            raise RuntimeError("没有任何一镜渲染成功")
        # 查每镜实际时长（转场偏移与字幕时间轴都要用）
        durs: List[float] = []
        for c in clips:
            durs.append(max(1.0, _probe_dur(c, ffmpeg)))
        merged = clips[0]
        # ---- xfade 串联（镜间叠化转场；n 镜 = n-1 次；两段都有音轨才做音频转场）----
        styles = ["fade", "circleopen", "smoothleft", "wiperight",
                  "fadeblack", "circlecrop"]
        for k in range(1, len(clips)):
            off = sum(durs[:k]) - k * XFD
            off = max(0.1, off)
            nxt = os.path.join(tmp, "m%02d.mp4" % k)
            vfc = (f"[0:v][1:v]xfade=transition={styles[k % len(styles)]}:"
                   f"duration={XFD}:offset={off:.2f}[v]")
            if _probe_audio(merged, ffmpeg) and _probe_audio(clips[k], ffmpeg):
                fcm = vfc + f";[0:a][1:a]acrossfade=d={XFD}[a]"
                mapargs = ["-map", "[v]", "-map", "[a]"]
            else:
                fcm = vfc
                mapargs = ["-map", "[v]", "-map", "0:a?"]
            _run_ff([ffmpeg, "-y", "-i", merged, "-i", clips[k],
                     "-filter_complex", fcm] + mapargs +
                    ["-c:v", "libx264", "-preset", "medium", "-crf", "22",
                     "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
                     nxt])
            merged = nxt
        # ---- 字幕烧录 + 首尾淡入淡出（srt 时间轴按转场偏移累计）----
        out = os.path.join(pkg_dir, "成片.mp4")
        total = sum(durs) - (len(clips) - 1) * XFD
        srt = os.path.join(tmp, "subs.srt")
        t0 = 0.0
        rows = []
        for idx, fr in enumerate(frames):
            if idx >= len(durs):
                break
            txt = (fr.get("subtitle") or fr.get("narration") or "").strip()
            if txt:
                rows.append(f"{idx + 1}\n"
                            f"{_srt_ts(t0)} --> {_srt_ts(t0 + max(1.0, durs[idx] - 0.4))}\n"
                            f"{txt[:60]}\n")
            t0 += durs[idx] - XFD
        has_subs = bool(rows)
        if has_subs:
            with open(srt, "w", encoding="utf-8-sig") as f:
                f.write("\n".join(rows))
        vf_final = ""
        if has_subs:
            # Windows 路径转义（C:\a\b → C\:/a/b）+ 中文字体样式
            srt_esc = srt.replace("\\", "/").replace(":", "\\:")
            vf_final = (f"subtitles='{srt_esc}'"
                        f":force_style='FontName=Microsoft YaHei,FontSize=15,"
                        f"PrimaryColour=&H00FFFFFF,OutlineColour=&H80000000,"
                        f"BorderStyle=1,Outline=1,Shadow=1,MarginV=28'")
        fade_out_at = max(0.1, total - 0.9)
        fade = (f"fade=t=in:st=0:d=0.6,fade=t=out:st={fade_out_at:.2f}:d=0.9")
        # 字幕滤镜在特殊路径（中文用户名/特殊字符）下可能失败 → 降级无字幕保成片
        try:
            vf_cmd = ",".join(x for x in (vf_final, fade) if x) or "null"
            _run_ff([ffmpeg, "-y", "-i", merged, "-vf", vf_cmd,
                     "-c:v", "libx264", "-preset", "medium", "-crf", "21",
                     "-pix_fmt", "yuv420p", "-c:a", "copy", out])
        except RuntimeError:
            if not has_subs:
                raise
            _run_ff([ffmpeg, "-y", "-i", merged, "-vf", fade,
                     "-c:v", "libx264", "-preset", "medium", "-crf", "21",
                     "-pix_fmt", "yuv420p", "-c:a", "copy", out])
        if not os.path.isfile(out) or os.path.getsize(out) < 1000:
            raise RuntimeError("mp4 合成结果为空")
        return out
    finally:
        try:
            for c in clips:
                if os.path.exists(c):
                    os.remove(c)
        except Exception:
            pass


def _probe_dur(path: str, ffmpeg: str) -> float:
    """用 ffmpeg -i 探测媒体时长（秒），失败返回 0。"""
    try:
        import subprocess
        proc = subprocess.run([ffmpeg, "-i", path], capture_output=True,
                              timeout=30, **_nowin_kw())
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)",
                      proc.stderr.decode("utf-8", "ignore"))
        if m:
            return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + \
                float(m.group(3))
    except Exception:
        pass
    return 0.0


def _srt_ts(sec: float) -> str:
    ms = max(0, int(round(sec * 1000)))
    h, r = divmod(ms, 3600000)
    m, r = divmod(r, 60000)
    s, ms = divmod(r, 1000)
    return "%02d:%02d:%02d,%03d" % (h, m, s, ms)


def _run_ff(cmd: List[str]):
    import subprocess
    proc = subprocess.run(cmd, capture_output=True, timeout=900, **_nowin_kw())
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "ignore")[-400:]
        raise RuntimeError("ffmpeg 失败：\n" + err)


def _probe_audio(path: str, ffmpeg: str) -> bool:
    """文件是否带音轨（决定 xfade 时能否做 acrossfade 音频转场）。"""
    import subprocess
    try:
        kw = {"creationflags": 0x08000000} if os.name == "nt" else {}
        p = subprocess.run([ffmpeg, "-i", path], capture_output=True,
                           timeout=30, **dict(kw, **_nowin_kw()))
        return b"Audio:" in (p.stderr or b"")
    except Exception:
        return False


def _audio_dur(mp3: Optional[str], ffmpeg: str) -> float:
    """配音时长：优先 ffprobe，其次按字数估算。"""
    if mp3 and os.path.isfile(mp3):
        try:
            ffp = os.path.join(os.path.dirname(ffmpeg),
                               "ffprobe" + (".exe" if sys.platform == "win32" else ""))
            if os.path.isfile(ffp):
                import subprocess
                out = subprocess.run([ffp, "-v", "error", "-show_entries",
                                      "format=duration", "-of", "default=noprint_wrappers=1:nokey=1",
                                      mp3], capture_output=True, timeout=30, **_nowin_kw())
                d = float((out.stdout or b"0").decode("utf-8", "ignore").strip() or 0)
                if d > 0.5:
                    return min(d, 30.0)
        except Exception:
            pass
        base = os.path.basename(mp3)
        # 文件名没存原文长度时按 4 秒兜底
        return 4.0
    return 4.0


# ---------------- 小工具 ----------------

def _as_uri(p: str) -> str:
    if not p:
        return ""
    try:
        from pathlib import Path
        u = Path(p).as_uri()
    except Exception:
        u = "file:///" + p.replace("\\", "/")
    return urllib.parse.quote(u, safe=":/%.@-")


def _esc(s: str) -> str:
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def slug(s: str, n: int = 24) -> str:
    s = re.sub(r"[\\/:*?\"<>|\s]+", "_", str(s or "").strip()).strip("._")
    return s[:n] or "未命名"


def safe_filename(kind: str, title: str, now=None) -> str:
    """产出子目录名（**相对工作根**）：`<分类>/<标题>_<时间>`（避免重名覆盖）。

    v0.30.11：第一个参数以前直接当目录名用（于是 image / manga / ppt_img 各占
    一个目录、互不相邻）；现在它是 kind，经 `workspace.cat_of` 归到 7 个用户
    可见分类之一。5 个调用点都写成
    `os.path.join(pick_out_root(cfg), safe_filename(kind, title))`，
    所以换语义即整体生效 —— 调用点一行都不用改。
    """
    import workspace as WS
    return WS.rel_pkg(kind, title, now)


def pick_out_root(cfg: dict) -> str:
    """产出根 = **统一工作根**（v0.30.11 起）。

    以前这里是 `ws_dir/创作`、默认 `桌面/PASM创作` —— 与项目（DATA_DIR/projects）、
    脚本、团队产物、工作流步骤产物凑成**四套互不相通的根**，用户根本找不齐自己的
    东西。现在统一到 `workspace.root()`（PASM_WORK_ROOT > PASM_STUDIO_DIR >
    cfg.ws_dir > 桌面/PASM工作），分类交给 `safe_filename`。
    """
    import workspace as WS
    return WS.ensure(cfg)
