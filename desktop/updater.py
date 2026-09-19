"""PASM Studio 升级逻辑（轻量、可降级）。

设计：
- 启动时（异步线程）从 APP_LATEST_URL 拉一份 latest.json，字段：
    {
      "version": "0.2.1",
      "download_url": "https://gitee.com/.../PASMStudio-Setup-0.2.1.exe",
      "notes": "修复... · 新增...",
      "released_at": "2026-09-02"
    }
- 若 remote > 本地版本，回调 update_available(remote_dict)，由 UI 弹通知
  （默认只打印日志，不自动下载/不强制升级）。
- 离线/超时/解析失败均不阻塞启动。
- 数据也允许来自本地 file:// 路径，便于内测。

扩展点：把"提示"接成 Qt 弹窗即可上线（示例 on_update 回调中）。
"""
from __future__ import annotations

import json
import os
import threading
import urllib.request
from typing import Callable, Optional

from appinfo import APP_LATEST_URL, APP_VERSION, is_newer

DEFAULT_TIMEOUT = 4  # 秒
CacheFile = os.path.join(os.path.expanduser("~"), ".pasmstudio_last_check.json")


def _fetch(url: str, timeout: float) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        data = r.read()
    return json.loads(data.decode("utf-8"))


def check_for_update(url: Optional[str] = None,
                     timeout: float = DEFAULT_TIMEOUT,
                     proxy: Optional[dict] = None) -> Optional[dict]:
    """同步拉取并比对；返回 latest 字典（若有新版），否则 None。"""
    url = url or APP_LATEST_URL
    try:
        data = _fetch(url, timeout)
    except Exception:
        # 尝试上次缓存（内测/离线）
        if os.path.exists(CacheFile):
            try:
                data = json.load(open(CacheFile, "r", encoding="utf-8"))
            except Exception:
                return None
        else:
            return None
    if not isinstance(data, dict) or "version" not in data:
        return None
    if is_newer(data["version"]):
        # 缓存成功响应以备离线
        try:
            json.dump(data, open(CacheFile, "w", encoding="utf-8"))
        except Exception:
            pass
        return data
    return None


def check_async(callback: Callable[[Optional[dict]], None],
                url: Optional[str] = None,
                timeout: float = DEFAULT_TIMEOUT):
    """异步版本：回调形如 `def cb(latest_or_none)`。"""
    def _worker():
        result = check_for_update(url, timeout)
        callback(result)
    threading.Thread(target=_worker, daemon=True).start()
