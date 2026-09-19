# -*- coding: utf-8 -*-
"""日志初始化 —— 必须在**任何 logging 调用之前**执行。

为什么单独成一个模块：``logging.basicConfig()`` 有个极隐蔽的语义 ——
**root logger 已有 handler 时，它静默变成 no-op**（不报错、不警告）。
而 ``logging.info()`` 这类模块级调用在没有 handler 时，会**自动做一次
无参 basicConfig()**（只装 stderr）。

于是只要启动路径上任何一处先调了 logging（例如入口里的迁移日志），
后面那句 ``basicConfig(filename=...)`` 就不再生效 —— **日志从此一个字都不写**。

真机踩到（v0.30.0）：入口新增的迁移日志先跑了 ``logging.info()``，
结果 ``pasm.log`` 一直是空的。用户报问题时完全无据可依，
而"没有日志"这件事本身也不报错，极难联想到顺序问题。
"""
from __future__ import annotations

import logging
import os
import sys
import time            # v0.30.15 修：_note_failure 曾用未导入的 time → 报错文件永远写不出来

_installed = False


def roaming_appdata() -> str:
    """取 Roaming AppData 的**权威**路径。

    为什么不直接用 ``os.environ["APPDATA"]``：它在某些启动方式下**取不到**
    （实测：从 bash / 服务方式启动打包版时是 None，而双击启动有值）。
    一旦取不到就会静默退化到用户主目录，于是数据目录**分裂成两份** ——
    双击启动写 ``AppData\\Roaming\\PASMStudio``，其他方式写 ``~\\PASMStudio``；
    用户会觉得"我的数据不见了"，排查时两边对不上。

    所以优先走 Win32 API（与资源管理器里看到的路径一致），环境变量只作兜底。
    """
    if os.name == "nt":
        try:
            import ctypes
            buf = ctypes.create_unicode_buffer(260)
            # CSIDL_APPDATA = 0x1A（即 Roaming）
            if ctypes.windll.shell32.SHGetFolderPathW(None, 0x1A, None, 0, buf) == 0:
                if buf.value:
                    return buf.value
        except Exception:
            pass
    return os.environ.get("APPDATA") or os.path.expanduser("~")


def data_dir() -> str:
    """应用数据目录 —— **与 pasm_companion.DATA_DIR 同一来源**，勿各自实现。"""
    if os.environ.get("PASM_STUDIO_DIR"):
        return os.environ["PASM_STUDIO_DIR"]
    if getattr(sys, "frozen", False):
        return os.path.join(roaming_appdata(), "PASMStudio")
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        ".pasmstudio_dev")


def setup(data_dir_: str | None = None, name: str = "pasm.log") -> str:
    """装好文件日志并返回日志路径（重复调用安全）。

    实现刻意**不依赖 logging.basicConfig** —— 那个 API 的隐式语义太多
    （root 有 handler 时静默 no-op、模块级 logging 调用会抢先自动配置），
    已经因此丢过一个版本的日志。这里手动装 handler，行为完全可控。
    """
    global _installed
    d = data_dir_ or data_dir()
    path = os.path.join(d, name)
    if _installed:
        return path
    root = logging.getLogger()
    try:
        os.makedirs(d, exist_ok=True)
        fh = logging.FileHandler(path, encoding="utf-8")
        fh.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(message)s"))
        # 同路径的旧 handler 先摘掉（幂等，避免重复写两遍）
        ap = os.path.abspath(path)
        for h in list(root.handlers):
            if isinstance(h, logging.FileHandler) and \
                    os.path.abspath(getattr(h, "baseFilename", "") or "") == ap:
                root.removeHandler(h)
        root.addHandler(fh)
        root.setLevel(logging.INFO)
    except Exception as ex:                       # noqa: BLE001
        # 装不上也要留痕：否则"日志永远是空的"这件事本身无据可查。
        _note_failure("setup 失败：%r（path=%s）" % (ex, path))
        return path
    _installed = True
    # 自检：真写一条并确认落到磁盘（不是"配了就算成功"）
    logging.info("---- 日志启动：%s ----", path)
    try:
        for h in root.handlers:
            h.flush()
        if not (os.path.isfile(path) and os.path.getsize(path) > 0):
            _note_failure("自检失败：配了 handler 但文件仍为空（path=%s）" % path)
    except Exception as ex:                       # noqa: BLE001
        _note_failure("自检异常：%r" % ex)
    return path


def _note_failure(msg: str) -> None:
    """日志装不上时，把原因写到临时文件 —— 症状要能被看见，不能靠猜。"""
    try:
        p = os.path.join(os.environ.get("TEMP") or os.path.expanduser("~"),
                         "pasm_logsetup_error.txt")
        with open(p, "a", encoding="utf-8") as f:
            f.write("%s  %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass
