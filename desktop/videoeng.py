"""videoeng —— 图生视频双引擎（v0.21.0）。

把漫剧/图文短片从"静态图 + Ken Burns 2D 运镜"升级为真视频：
分镜图作为首帧 + 每镜动作提示词 → 图生视频模型生成 5~10 秒真实动态片段
（人物会动、镜头会推、环境会流动），再交 render_mp4 组装成片。

provider（cfg["video_provider"]）：
- "seedance"  火山方舟 Ark API（即梦同源 Doubao-Seedance 模型，云端）
              用户在 console.volcengine.com/ark 开通模型并创建 API Key，
              填到 设置 → 视频引擎 → 方舟 API Key（cfg["ark_key"]）。
              模型默认 doubao-seedance-1-5-pro-251215（在售、支持图生视频），
              可在设置下拉里换 2.0 系 / 1.0 pro（cfg["ark_model"]）。
              注意：旧版默认 doubao-seedance-1-0-lite-i2v-250428 已下线，
              方舟对不存在的模型返回 404——运行时会自动迁移到当前默认；
              「方舟视频带同步音频」开关（cfg["ark_audio"]）仅 1.5 系生效。
- "comfyui"   本地 ComfyUI（Wan2.2 / LTX-Video 等开源模型，零 API 费用）
              需本机运行 ComfyUI（默认 http://127.0.0.1:8188，可改 cfg["comfy_url"]），
              并把 ComfyUI「Export (API)」导出的 workflow JSON 存到
              %APPDATA%/PASMStudio/comfy_i2v_workflow.json（可改 cfg["comfy_workflow"]）。
              我们会自动替换其中的首帧图与正向提示词，其余参数以 workflow 为准。
- "none"      未启用 → 调用方退回 Ken Burns 模式（零成本兜底，老功能不丢）

统一入口：
  provider_of(cfg) -> str                 当前引擎（none/seedance/comfyui）
  gen_i2v(cfg, image_path, motion, out_path, duration=5, progress=None) -> str
      生成动态片段存到 out_path；失败抛 RuntimeError（中文可读），
      调用方逐镜 try/except 降级为静态图即可。
"""
from __future__ import annotations

import base64
import json
import os
import sys
import time
import urllib.parse
import uuid

_ARK_BASE = "https://ark.cn-beijing.volces.com/api/v3"
# v0.23.1：旧默认 doubao-seedance-1-0-lite-i2v-250428 已在方舟下线
# （对不存在/未开通的模型，方舟直接返回 404 ModelNotFound——这就是用户看到的
# 「调用 404」）。现按 2026-09 方舟在售模型列表更新：
_ARK_MODEL_CHOICES = [
    "doubao-seedance-1-5-pro-251215",       # 推荐：图生视频，画质/速度均衡，可选同步音频
    "doubao-seedance-2-0-260128",           # 2.0：画质更强（支持有声/参考图）
    "doubao-seedance-2-0-fast-260128",      # 2.0 fast：更快
    "doubao-seedance-2-0-mini-260615",      # 2.0 mini：更便宜
    "doubao-seedance-1-0-pro-250528",       # 1.0 pro
    "doubao-seedance-1-0-pro-fast-251015",  # 1.0 pro fast
]
_DEFAULT_ARK_MODEL = _ARK_MODEL_CHOICES[0]
# 已下线的历史模型 → 运行时自动迁移到当前默认（含设置里存了旧值的用户）
_RETIRED_ARK_MODELS = {
    "doubao-seedance-1-0-lite-i2v-250428",
    "doubao-seedance-1-0-lite-i2v-250219",
    "doubao-seedance-1-0-lite-t2v-250219",
    "doubao-seedance-1-0-lite-t2v-250428",
}
_ARK_TIMEOUT_CREATE = 60
_ARK_TIMEOUT_TASK = 900          # lite 720p 5s 一般 1~2 分钟，给足余量
_COMFY_TIMEOUT_TASK = 1800       # 本地 5B 模型一镜可能好几分钟

# 本地 workflow 识别：图生视频类节点（用于定位正向提示词引用）
_I2V_CLASSES = {"WanImageToVideo", "Wan22ImageToVideoLatent",
                "LTXImageToVideo", "LTXVConditioning", "LTXVImgToVideo",
                "CogVideoXImageToVideo", "HunyuanImageToVideo",
                "HunyuanVideoImg2V", "SVD_img2vid_Conditioning"}
# 输出保存节点（历史里可能带视频文件的节点类型）
_SAVE_CLASSES = {"VHS_VideoCombine", "SaveVideo", "SaveAnimatedWEBP",
                 "SaveAnimatedPNG", "SaveWEBM"}


def provider_of(cfg: dict) -> str:
    """当前视频引擎（none / seedance / comfyui）。"""
    p = str((cfg or {}).get("video_provider") or "none").strip().lower()
    return p if p in ("none", "seedance", "comfyui") else "none"


def _default_workflow_path() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "PASMStudio", "comfy_i2v_workflow.json")


def ready(cfg: dict) -> tuple:
    """引擎是否真正就绪 → (ok: bool, 原因: str)。

    v0.22.1：成片前预检，防止"选了 ComfyUI 却没放 workflow / 没填方舟 Key /
    服务没启动"时，一路静默降级成静态图 + 配音——用户还以为是引擎不行。
    调用方在整片开头检查一次，未就绪就明确提示原因，本片跳过逐镜 i2v。
    """
    p = provider_of(cfg)
    if p == "none":
        return True, ""
    if p == "seedance":
        if not str((cfg or {}).get("ark_key") or "").strip():
            return (False, "方舟 API Key 未填：设置 → 视频引擎 → 方舟 API Key，"
                           "粘贴后重试（即梦 Seedance 需要）")
        return True, ""
    if p == "comfyui":
        wf = str((cfg or {}).get("comfy_workflow") or _default_workflow_path())
        if not os.path.isfile(wf):
            return (False,
                    "未找到 ComfyUI 图生视频 workflow：\n" + wf +
                    "\n\n请到 设置 → 视频引擎 → 点「🧰 ComfyUI 一键配置向导」，"
                    "向导会检测你已装的模型并引导 3 步生成这份文件"
                    "（不用自己搭工作流）。")
        ok, probs = verify_workflow(wf)
        if not ok:
            return (False, "workflow 文件存在但自检不通过（导入的可能不完整）：\n- "
                    + "\n- ".join(probs[:4]))
        url = str((cfg or {}).get("comfy_url") or "http://127.0.0.1:8188").rstrip("/")
        try:
            import urllib.request
            with urllib.request.urlopen(url + "/system_stats", timeout=2):
                return True, ""
        except Exception:
            return (False, "连不上本地 ComfyUI（" + url +
                    "）。请先启动 ComfyUI，再点上面的「重发」重新生成。")
    return True, ""


def _say(progress, msg: str):
    if progress:
        try:
            progress(msg)
        except Exception:
            pass


# ================= 共用小工具 =================
def _b64_data_url(path: str) -> str:
    """本地图片 → data URL（Ark 图生视频支持 base64 上图，免图床）。"""
    ext = "jpeg" if str(path).lower().endswith((".jpg", ".jpeg")) else "png"
    with open(path, "rb") as f:
        return "data:image/%s;base64,%s" % (ext, base64.b64encode(f.read()).decode())


def _json_req(url: str, body: dict, headers: dict, timeout: int = 60):
    import urllib.request
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))


def _get_json(url: str, headers: dict, timeout: int = 60):
    import urllib.request
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))


def _ark_error_text(ex: Exception, model: str = "") -> str:
    """把 urllib HTTPError 转成可读中文：带出方舟返回的 error code/message。

    之前只透出「HTTP Error 404: Not Found」，用户无法知道是模型下线还是
    Key 问题——现在解析响应体里的 {"error":{"code","message"}} 给可行动提示。
    """
    code = getattr(ex, "code", None)
    detail = ""
    try:
        raw = ex.read().decode("utf-8", "ignore") if hasattr(ex, "read") else ""
        if raw:
            d = json.loads(raw)
            err = d.get("error") or {}
            detail = str(err.get("code") or "") + " " + \
                str(err.get("message") or d.get("message") or "")
    except Exception:
        pass
    detail = detail.strip() or str(ex)
    mtag = ("（当前模型：%s）" % model) if model else ""
    if code == 401:
        return "鉴权失败（401）：" + detail + "。API Key 无效或已过期，请到方舟控制台重新生成。"
    if code == 403:
        return "无权限（403）：" + detail + "。当前账号/Key 未开通该模型服务" + mtag + \
               "，请到 console.volcengine.com/ark 开通。"
    if code == 404:
        return "模型不存在（404）：" + detail + "。" + mtag + \
               " 已下线模型（如 lite 系列）或未开通的模型都会返回 404——" \
               "请到 console.volcengine.com/ark 查看在售模型并开通，" \
               "推荐 doubao-seedance-1-5-pro-251215。"
    if code == 429:
        return "触发限流（429）：" + detail + "。稍等片刻再试，或在方舟控制台提升并发额度。"
    if code and code >= 500:
        return "方舟服务端错误（HTTP %s）：%s。通常稍后重试即可。" % (code, detail)
    return ("HTTP %s：%s" % (code, detail)) if code else detail


def _download(url: str, out_path: str, headers: dict | None = None) -> str:
    import urllib.request
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=600) as r, \
            open(out_path, "wb") as f:
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
    if not os.path.isfile(out_path) or os.path.getsize(out_path) < 50_000:
        raise RuntimeError("视频片段下载结果异常（文件过小）")
    return out_path


def _mp4_multipart(image_path: str) -> tuple:
    """构造 multipart/form-data（ComfyUI /upload/image 的 image 字段）。

    返回 (body_bytes, content_type)。
    """
    boundary = "----PASMStudio" + uuid.uuid4().hex
    fname = os.path.basename(image_path) or "frame.png"
    with open(image_path, "rb") as f:
        data = f.read()
    parts = [
        ("--%s\r\nContent-Disposition: form-data; name=\"image\"; filename=\"%s\"\r\n"
         "Content-Type: application/octet-stream\r\n\r\n" % (boundary, fname)).encode()
        + data + b"\r\n",
        ("--%s\r\nContent-Disposition: form-data; name=\"overwrite\"\r\n\r\ntrue\r\n"
         % boundary).encode(),
        ("--%s\r\nContent-Disposition: form-data; name=\"type\"\r\n\r\ninput\r\n"
         % boundary).encode(),
        ("--%s--\r\n" % boundary).encode(),
    ]
    return b"".join(parts), "multipart/form-data; boundary=%s" % boundary


# ================= 云引擎：火山方舟 Seedance（即梦同源） =================
def _seedance_gen(cfg: dict, image_path: str, motion: str,
                  out_path: str, duration: int, progress) -> str:
    key = str(cfg.get("ark_key") or "").strip()
    if not key:
        raise RuntimeError("还没填方舟 API Key：到 设置 → 视频引擎 填入后重试"
                           "（console.volcengine.com/ark 开通模型并创建 Key）。")
    if not os.path.isfile(image_path):
        raise RuntimeError("首帧图不存在：" + image_path)
    model = str(cfg.get("ark_model") or _DEFAULT_ARK_MODEL).strip()
    if model in _RETIRED_ARK_MODELS:
        _say(progress, "…模型 %s 已在方舟下线，自动改用 %s" % (model, _DEFAULT_ARK_MODEL))
        model = _DEFAULT_ARK_MODEL
    headers = {"Authorization": "Bearer " + key}
    body = {
        "model": model,
        "content": [
            {"type": "text", "text": motion},
            {"type": "image_url", "image_url": {"url": _b64_data_url(image_path)}},
        ],
        "resolution": "720p",
        "ratio": "adaptive",
        "duration": 10 if int(duration or 5) > 5 else 5,
        "watermark": False,
    }
    # 1.5 pro 支持「带同步音频」参数（1.0 系不认识该参数，强校验会报错——只对 1-5 加）。
    # 默认关：漫剧流程有自己的 TTS 配音，画面自带声音会叠。
    if "1-5" in model:
        body["generate_audio"] = bool(cfg.get("ark_audio", False))
    _say(progress, "…提交图生视频任务（%s）" % model)
    try:
        d = _json_req(_ARK_BASE + "/contents/generations/tasks",
                      body, headers, timeout=_ARK_TIMEOUT_CREATE)
    except Exception as ex:
        raise RuntimeError("方舟任务提交失败：" + _ark_error_text(ex, model))
    task_id = str(d.get("id") or "")
    if not task_id:
        raise RuntimeError("方舟任务提交异常（未返回任务 id）：" + json.dumps(
            d, ensure_ascii=False)[:200])

    _say(progress, "…生成中（任务 %s，一般 1~2 分钟）" % task_id)
    t0 = time.time()
    last = ""
    while time.time() - t0 < _ARK_TIMEOUT_TASK:
        time.sleep(5)
        try:
            d = _get_json(_ARK_BASE + "/contents/generations/tasks/" + task_id,
                          headers)
        except Exception as ex:
            last = str(ex)
            continue
        status = str(d.get("status") or "").lower()
        if status == "succeeded":
            url = ((d.get("content") or {}).get("video_url") or "")
            if not url:
                raise RuntimeError("任务完成但未返回视频地址：" + json.dumps(
                    d, ensure_ascii=False)[:200])
            _say(progress, "…片段已生成，正在下载")
            return _download(url, out_path)
        if status in ("failed", "expired", "cancelled"):
            err = json.dumps(d.get("error") or d, ensure_ascii=False)[:200]
            raise RuntimeError("图生视频任务%s：%s" % (
                "失败" if status == "failed" else "超时/取消", err))
        _say(progress, "…生成中 %ds（状态 %s）" % (int(time.time() - t0), status or "…"))
    raise RuntimeError("图生视频超时（%ds）：%s" % (_ARK_TIMEOUT_TASK, last or task_id))


# ================= 本地引擎：ComfyUI（Wan2.2 / LTX-Video 等） =================
def _comfy_gen(cfg: dict, image_path: str, motion: str,
               out_path: str, duration: int, progress) -> str:
    url = str(cfg.get("comfy_url") or "http://127.0.0.1:8188").rstrip("/")
    wf_path = str(cfg.get("comfy_workflow") or _default_workflow_path())
    if not os.path.isfile(wf_path):
        raise RuntimeError(
            "未找到 ComfyUI 图生视频 workflow：%s。请先在 ComfyUI 里搭好"
            "图生视频流程（Wan2.2 / LTX 等均可），点 Export (API) 导出 JSON，"
            "存到上面这个路径后重试。" % wf_path)
    if not os.path.isfile(image_path):
        raise RuntimeError("首帧图不存在：" + image_path)
    try:
        with open(wf_path, encoding="utf-8") as f:
            wf = json.load(f)
    except Exception as ex:
        raise RuntimeError("workflow JSON 读取失败：" + str(ex))
    if not isinstance(wf, dict) or not wf:
        raise RuntimeError("workflow JSON 内容异常（应为 API 导出的节点字典）。")

    # 1) 上传首帧 → 服务器文件名
    body, ctype = _mp4_multipart(image_path)
    import urllib.request
    try:
        req = urllib.request.Request(url + "/upload/image", data=body,
                                     headers={"Content-Type": ctype})
        with urllib.request.urlopen(req, timeout=60) as r:
            up = json.loads(r.read().decode("utf-8", "ignore"))
    except Exception as ex:
        raise RuntimeError("上传首帧到 ComfyUI 失败（服务没开？%s）：" % url + str(ex))
    server_img = str((up or {}).get("name") or "")
    if not server_img:
        raise RuntimeError("ComfyUI 上传返回异常：" + json.dumps(up, ensure_ascii=False)[:120])

    # 2) 替换首帧（第一个 LoadImage）
    loads = [nid for nid, n in wf.items()
             if isinstance(n, dict) and n.get("class_type") == "LoadImage"]
    if loads:
        wf[loads[0]].setdefault("inputs", {})["image"] = server_img

    # 3) 替换正向提示词：优先取图生视频节点 positive 引用的编码器；
    #    兜底取未被 negative 引用的 CLIPTextEncode
    pos_id = None
    neg_ids = set()
    for n in wf.values():
        if not isinstance(n, dict):
            continue
        if n.get("class_type") in _I2V_CLASSES:
            ref = (n.get("inputs") or {}).get("positive")
            if isinstance(ref, list) and ref and ref[0] in wf:
                pos_id = ref[0]
        nr = (n.get("inputs") or {}).get("negative")
        if isinstance(nr, list) and nr:
            neg_ids.add(nr[0])
    if pos_id is None:
        encs = [nid for nid, n in wf.items()
                if isinstance(n, dict) and n.get("class_type") == "CLIPTextEncode"
                and nid not in neg_ids]
        pos_id = encs[0] if encs else None
    if pos_id and "text" in wf[pos_id].get("inputs", {}):
        wf[pos_id]["inputs"]["text"] = motion
    else:
        raise RuntimeError("workflow 里没找到可替换的正向提示词节点"
                           "（需含 CLIPTextEncode 或图生视频节点）。")

    # 4) 提交生成
    try:
        d = _json_req(url + "/prompt",
                      {"prompt": wf, "client_id": "pasmstudio"}, {}, timeout=60)
    except Exception as ex:
        raise RuntimeError("提交 ComfyUI 任务失败：" + str(ex))
    pid = str(d.get("prompt_id") or "")
    if not pid:
        raise RuntimeError("ComfyUI 提交异常：" + json.dumps(d, ensure_ascii=False)[:200])

    # 5) 轮询 /history/{pid}，收集任意保存节点里的文件项
    _say(progress, "…本地生成中（%s）" % url)
    t0 = time.time()
    while time.time() - t0 < _COMFY_TIMEOUT_TASK:
        time.sleep(3)
        try:
            hist = _get_json(url + "/history/" + pid, {})
        except Exception:
            continue
        h = (hist or {}).get(pid) or {}
        if h.get("status") and str(h["status"].get("status_str", "")).lower() == "error":
            msgs = "; ".join(m or "" for m in
                             (h["status"].get("messages") or [["", ""]][0][1:2] and
                              [str(h["status"]["messages"][-1])]))[:200]
            raise RuntimeError("ComfyUI 执行出错：" + msgs)
        outs = h.get("outputs") or {}
        item = None
        for _nid, o in outs.items():
            for key in ("gifs", "videos", "images"):
                for it in (o.get(key) or []):
                    if it.get("filename") and it.get("type") == "output":
                        item = it
                        break
                if item:
                    break
            if item:
                break
        if item:
            view = ("%s/view?filename=%s&subfolder=%s&type=output"
                    % (url, urllib.parse.quote(item["filename"]),
                       urllib.parse.quote(item.get("subfolder") or "")))
            _say(progress, "…片段已生成，正在下载 %s" % item["filename"])
            return _download(view, out_path)
        _say(progress, "…本地生成中 %ds" % int(time.time() - t0))
    raise RuntimeError("本地生成超时（%ds）——请确认显存足够、模型已加载，"
                       "或换更快的模型（如 LTX-Video）。" % _COMFY_TIMEOUT_TASK)


# ================= 统一入口 =================
def gen_i2v(cfg: dict, image_path: str, motion: str, out_path: str,
            duration: int = 5, progress=None) -> str:
    """图生视频：首帧图 + 动作提示词 → 动态片段 MP4 存 out_path。"""
    p = provider_of(cfg)
    motion = (motion or "").strip() or "镜头缓慢推进，画面自然流动"
    if p == "seedance":
        return _seedance_gen(cfg, image_path, motion, out_path,
                             duration, progress)
    if p == "comfyui":
        return _comfy_gen(cfg, image_path, motion, out_path,
                          duration, progress)
    raise RuntimeError("未启用视频引擎（设置 → 视频引擎）")


# ============ v0.22.2 ComfyUI 配置向导支撑（检测/校验/示例） ============
_SAMPLE_NAMES = {
    "wan22": "wan22_image_to_video_5B.json",     # 官方 Wan2.2 I2V 5B（ComfyUI 原生节点）
    "ltx": "ltx_image_to_video.json",            # 官方 LTX-Video I2V
}
_MODEL_KEY = ("wan", "ltx", "ltxv", "ltx-video", "hunyuan", "cogvideox", "svd",
              "i2v", "ti2v", "img2vid", "image_to_video", "5b", "0.9.5")


def _sample_dir() -> str:
    """官方示例 workflow（UI 格式，供导入 ComfyUI）所在目录。"""
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
        for cand in (os.path.join(base, "assets", "comfy_samples"),
                     os.path.join(base, "_internal", "assets", "comfy_samples")):
            if os.path.isdir(cand):
                return cand
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "assets", "comfy_samples")


def list_samples() -> list:
    d = _sample_dir()
    try:
        return sorted(f for f in os.listdir(d) if f.endswith(".json"))
    except Exception:
        return []


def _oi(url: str) -> dict:
    """拉取 ComfyUI /object_info（节点定义）。失败抛 RuntimeError。"""
    import urllib.request
    try:
        req = urllib.request.Request(url + "/object_info")   # timeout 走 urlopen
        with urllib.request.urlopen(req, timeout=20) as r:
            d = json.loads(r.read().decode("utf-8", "ignore"))
        return d if isinstance(d, dict) else {}
    except Exception as ex:
        raise RuntimeError("连不上 ComfyUI（%s）：%s\n请先启动 ComfyUI（默认"
                           " http://127.0.0.1:8188）再点「重新检测」。" % (url, ex))


def comfy_probe(cfg: dict) -> dict:
    """检测 ComfyUI：服务是否可达 + 已装哪些与图生视频相关的节点。"""
    url = str((cfg or {}).get("comfy_url") or "http://127.0.0.1:8188").rstrip("/")
    out = {"url": url, "ok": False, "version": "", "nodes": [], "error": ""}
    try:
        import urllib.request
        req = urllib.request.Request(url + "/system_stats")  # timeout 走 urlopen
        with urllib.request.urlopen(req, timeout=6) as r:
            st = json.loads(r.read().decode("utf-8", "ignore"))
        out["version"] = str(((st.get("system") or {}).get("comfyui_version")
                              or "未知"))
        oi = _oi(url)
        have = set(oi.keys())
        out["nodes"] = sorted(_I2V_CLASSES & have)
        out["ok"] = True
        out["error"] = ""
    except Exception as ex:
        out["error"] = ("连不上 ComfyUI（%s）：%s\n请先启动 ComfyUI（默认"
                        " http://127.0.0.1:8188）再点「① 检测」。" % (url, ex))
    return out


def comfy_model_files(cfg: dict, limit: int = 30) -> list:
    """从 object_info 里收集与 i2v 相关的模型文件名（unet/ckpt/vae 等）。"""
    url = str((cfg or {}).get("comfy_url") or "http://127.0.0.1:8188").rstrip("/")
    oi = _oi(url)
    found, seen = [], set()
    for node in oi.values():
        if not isinstance(node, dict):
            continue
        inputs = node.get("input", {})
        for grp in ("required", "optional"):
            for _name, spec in (inputs.get(grp) or {}).items():
                opts = spec[0] if isinstance(spec, list) and spec else None
                if not isinstance(opts, list):
                    continue
                for o in opts:
                    if isinstance(o, str) and (".safetensors" in o.lower()
                                               or ".ckpt" in o.lower()
                                               or ".gguf" in o.lower()) \
                            and any(k in o.lower() for k in _MODEL_KEY) \
                            and o not in seen:
                        seen.add(o)
                        found.append(o)
    found.sort()
    return found[:limit]


def verify_workflow(path: str) -> tuple:
    """对 workflow JSON 做与运行时一致的静态校验。

    返回 (ok: bool, problems: list[str])：文件存在 / JSON 为节点字典 /
    有 LoadImage / 有可替换正向提示词的 i2v 或 CLIPTextEncode / 有输出节点。
    """
    probs = []
    if not path or not os.path.isfile(path):
        return False, ["workflow 文件不存在：" + str(path)]
    try:
        with open(path, encoding="utf-8") as f:
            wf = json.load(f)
    except Exception as ex:
        return False, ["workflow JSON 读取失败：" + str(ex)]
    if not isinstance(wf, dict) or not wf:
        return False, ["workflow JSON 内容异常（应为节点字典）。"]
    cls = {str(nid): (n.get("class_type") or "") for nid, n in wf.items()
           if isinstance(n, dict)}
    if not cls:
        return False, ["workflow 里没有任何节点。"]
    if "LoadImage" not in cls.values():
        probs.append("缺少 LoadImage（首帧图入口）节点")
    gens = [c for c in cls.values() if c in _I2V_CLASSES]
    if not gens:
        probs.append("没找到图生视频生成节点（需要：WanImageToVideo / "
                     "Wan22ImageToVideoLatent / LTXVImgToVideo / LTXImageToVideo…）")
    # 正向提示词可替换性
    pos_id = None
    neg = set()
    for nid, n in wf.items():
        if not isinstance(n, dict):
            continue
        inp = n.get("inputs") or {}
        if n.get("class_type") in _I2V_CLASSES and isinstance(inp.get("positive"), list) \
                and inp["positive"]:
            pos_id = inp["positive"][0]
        nr = inp.get("negative")
        if isinstance(nr, list) and nr:
            neg.add(nr[0])
    if pos_id is None:
        for nid, n in wf.items():
            if isinstance(n, dict) and n.get("class_type") == "CLIPTextEncode" \
                    and nid not in neg:
                pos_id = nid
                break
    if pos_id is None or "text" not in ((wf.get(pos_id) or {}).get("inputs") or {}):
        probs.append("没找到可替换的正向提示词（需 CLIPTextEncode）")
    if not any("save" in c.lower() or c in _SAVE_CLASSES or "VHS" in c
               for c in cls.values()):
        probs.append("缺少输出保存节点（SaveWEBM / SaveVideo / VHS_VideoCombine…）")
    return (not probs), probs


def save_sample_to(cfg: dict, kind: str, dst: str) -> tuple:
    """把内置的官方示例 UI workflow 复制到 dst（导入 ComfyUI 用）。"""
    fn = _SAMPLE_NAMES.get(kind or "")
    if not fn:
        return False, "没有这个示例：" + str(kind)
    src = os.path.join(_sample_dir(), fn)
    if not os.path.isfile(src):
        return False, "内置示例缺失（打包不完整）：" + src
    try:
        import shutil
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src, dst)
        return True, dst
    except Exception as ex:
        return False, "保存失败：" + str(ex)


def import_workflow(src: str, cfg: dict) -> tuple:
    """导入已有 API workflow：静态校验通过后复制到引擎默认路径。"""
    ok, probs = verify_workflow(src)
    if not ok:
        return False, "这份 workflow 自检不通过：\n- " + "\n- ".join(probs)
    dst = str((cfg or {}).get("comfy_workflow") or _default_workflow_path())
    try:
        import shutil
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src, dst)
        return True, dst
    except Exception as ex:
        return False, "写入失败：" + str(ex)


# ============ v0.22.4 ComfyUI 本机托管（查找 / 启动 / 停止） ============
# 说明：安装包不含 ComfyUI（本体+模型数十 GB、需独立安装）。这里做的是——
# 检测用户本机已装的 ComfyUI，可选择"随 PASM 启动/退出自动拉起与关闭"。
_COMFY_PROC = None


def comfy_running(cfg: dict, timeout: float = 1.5) -> bool:
    """ComfyUI 服务是否已在运行（轻量探活）。"""
    url = str((cfg or {}).get("comfy_url") or "http://127.0.0.1:8188").rstrip("/")
    import urllib.request
    try:
        with urllib.request.urlopen(url + "/system_stats", timeout=timeout):
            return True
    except Exception:
        return False


def find_comfy_main(cfg: dict) -> str:
    """在本机找 ComfyUI 的 main.py。先看显式配置，再扫常见位置。"""
    ex = str((cfg or {}).get("comfy_main") or "").strip()
    if ex and os.path.isfile(ex) and ex.endswith("main.py"):
        return ex
    import glob as _g
    roots = []
    home = os.path.expanduser("~")
    for sub in ("Desktop", "Downloads", ""):
        roots.append(os.path.join(home, sub))
    for d in ("C:/", "D:/", "E:/", "F:/", "G:/"):
        if os.path.exists(d):
            roots.append(d)
    seen = set()
    for root in roots:
        if not os.path.isdir(root):
            continue
        try:
            for ent in os.listdir(root):
                if "comfyui" not in ent.lower():
                    continue
                main = os.path.join(root, ent, "ComfyUI", "main.py")
                if ent.endswith("_portable") or os.path.isdir(os.path.join(root, ent, "ComfyUI")):
                    if os.path.isfile(main) and main not in seen:
                        seen.add(main)
                        return main
                main2 = os.path.join(root, ent, "main.py")
                if os.path.isfile(main2) and main2 not in seen:
                    seen.add(main2)
                    return main2
        except Exception:
            continue
    # 便携版也可能在 ComfyUI_windows_portable 下的标准结构
    for pat in (os.path.join(home, "ComfyUI_windows_portable", "ComfyUI", "main.py"),
                os.path.join(home, "Desktop", "ComfyUI_windows_portable", "ComfyUI", "main.py"),
                os.path.join(home, "Downloads", "ComfyUI_windows_portable", "ComfyUI", "main.py"),
                "D:/ComfyUI_windows_portable/ComfyUI/main.py",
                "E:/ComfyUI_windows_portable/ComfyUI/main.py",
                "C:/ComfyUI_windows_portable/ComfyUI/main.py"):
        if os.path.isfile(pat):
            return pat
    return ""


def _comfy_exec(main_py: str) -> str:
    """为 ComfyUI 挑 python：便携版优先其自带 python_embeded，其次系统 python。"""
    root = os.path.dirname(main_py)                    # …/ComfyUI
    for cand in (os.path.join(os.path.dirname(root), "python_embeded", "python.exe"),
                 os.path.join(os.path.dirname(os.path.dirname(root)), "python_embeded", "python.exe"),
                 os.path.join(os.path.dirname(root), "..", "..", "python_embeded", "python.exe")):
        if os.path.isfile(cand):
            return cand
    import shutil
    for name in ("pythonw.exe", "python.exe"):
        hit = shutil.which(name)
        if hit:
            return hit
    return ""


def start_comfy(cfg: dict, wait: float = 45.0) -> tuple:
    """启动本机 ComfyUI 并等它就绪。返回 (ok, msg)。进程句柄留在模块内供停止。"""
    global _COMFY_PROC
    if comfy_running(cfg):
        return True, "ComfyUI 已在运行。"
    main = find_comfy_main(cfg)
    if not main:
        return (False, "没在本机找到 ComfyUI。\n① 到 ComfyUI 官网下载 Windows 便携版"
                       "（ComfyUI_windows_portable），解压即用；\n② 或点向导里的"
                       "「选择 ComfyUI main.py…」手动定位你已装的 ComfyUI。")
    exe = _comfy_exec(main)
    if not exe:
        return False, "找到 ComfyUI 但找不到可用的 python（" + main + "）"
    url = str((cfg or {}).get("comfy_url") or "http://127.0.0.1:8188").rstrip("/")
    port = "8188"
    try:
        port = str(url.rsplit(":", 1)[-1].split("/")[0] or "8188")
    except Exception:
        pass
    import subprocess
    kw = {"creationflags": 0x08000000} if os.name == "nt" else {}     # 不弹黑窗
    try:
        _COMFY_PROC = subprocess.Popen(
            [exe, main, "--port", port],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw)
    except Exception as ex:
        return False, "启动 ComfyUI 失败：" + str(ex)
    t0 = time.time()
    while time.time() - t0 < wait:
        if comfy_running(cfg, timeout=1.0):
            return True, ("ComfyUI 已启动并就绪（端口 " + port + "）。"
                          "首次启动可能要装/加载模型，点「① 检测」确认节点与模型。")
        time.sleep(1.0)
    return (False, "ComfyUI 进程已拉起但 " + str(int(wait)) +
            " 秒内未就绪（首次启动需下载依赖或加载较慢）。请稍后在浏览器打开 "
            + url + " 确认，或重新点「▶ 启动」。")


def stop_comfy(cfg: dict) -> str:
    """停止由本应用拉起的 ComfyUI（仅影响本应用启动的那个进程）。"""
    global _COMFY_PROC
    if comfy_running(cfg):
        pass                                        # 服务在 → 交给进程关闭
    p = _COMFY_PROC
    _COMFY_PROC = None
    if p is None:
        if not comfy_running(cfg):
            return "ComfyUI 未在运行。"
        return "ComfyUI 不是由本应用启动的，需在它的窗口/任务管理器里关闭。"
    try:
        p.terminate()
        try:
            p.wait(timeout=4)
        except Exception:
            p.kill()
    except Exception:
        try:
            p.kill()
        except Exception:
            pass
    # 兜底：Windows 下连子进程一起结束
    if os.name == "nt":
        try:
            import subprocess
            subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"],
                           capture_output=True, timeout=10,
                           creationflags=0x08000000)
        except Exception:
            pass
    return "已停止由本应用启动的 ComfyUI。"
