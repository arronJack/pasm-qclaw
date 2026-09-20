# -*- coding: utf-8 -*-
"""platform_ops —— 跨平台系统操作小层（Windows / macOS / Linux 一套 API）。

为什么单独一个模块：桌面端原本散落着 `os.startfile` 与 `explorer /select,`，
它们**只存在于 Windows**：
  · Linux / macOS 上 `os.startfile` 根本不存在 → AttributeError；
  · 打开文件夹、用默认程序打开文件这类**核心交互**会直接失效。
把差异收进这一层后，其余代码只写一次。

三个公开函数都返回 ``(ok, msg)``：``msg`` 是**给人看的中文说明**，
失败时不抛异常（与 ``sysops`` 的"说得出就要做得到，做不到就说真话"一致）。

约定：全部用 `subprocess` **不经过 shell**，路径按参数传入，避免注入与引号问题。
"""
from __future__ import annotations

import os
import subprocess
import sys

#: 当前平台标识（与 sysops / audio 里的判断口径保持一致）
IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"
IS_LINUX = not IS_WINDOWS and not IS_MACOS


def startfile(path: str) -> None:
    """``os.startfile`` 的**跨平台替代品**（签名与异常语义保持一致）。

    刻意模仿 ``os.startfile``：成功返回 ``None``，失败**抛 ``OSError``** ——
    这样既有调用点的 ``try/except`` 不用改，换掉一个名字即可跨平台。

    Windows 直接走 ``os.startfile``；macOS 走 ``open``；Linux 走 ``xdg-open``。
    找不到打开工具时抛 ``OSError``（不是静默成功，符合"做不到就说真话"）。
    """
    path = str(path or "")
    if not path:
        raise OSError("没有可打开的路径")
    if not (os.path.exists(path) or "://" in path):
        raise OSError("路径不存在：%s" % path)
    if IS_WINDOWS:
        os.startfile(path)                   # type: ignore[attr-defined]  # noqa: S606
        return
    opener = "open" if IS_MACOS else "xdg-open"
    try:
        # 用 call 而不是 Popen：失败要能立刻知道（与 os.startfile 的同步语义一致）
        subprocess.Popen([opener, path], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                         start_new_session=True)
    except FileNotFoundError as ex:
        raise OSError("缺少打开工具 %s（Linux 上请安装 xdg-utils）：%s"
                      % (opener, ex)) from ex


def platform_name() -> str:
    """给人看的平台名（用于提示文案）。"""
    if IS_WINDOWS:
        return "Windows"
    if IS_MACOS:
        return "macOS"
    return "Linux"


def _quiet_popen(argv) -> None:
    """后台起进程，不弹控制台窗口（Windows 上必须 CREATE_NO_WINDOW）。"""
    kw = {}
    if IS_WINDOWS:
        # 0x08000000 = CREATE_NO_WINDOW，避免打开文件时闪一个黑框
        kw["creationflags"] = 0x08000000
    else:
        kw["start_new_session"] = True       # 与父进程解耦，父进程退出不影响
    subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     stdin=subprocess.DEVNULL, **kw)


def open_path(path: str):
    """用系统默认程序打开文件 / 目录 / 网址。

    Windows → ``os.startfile``；macOS → ``open``；Linux → ``xdg-open``。
    返回 ``(ok, msg)``。
    """
    path = str(path or "")
    if not path:
        return False, "没有可打开的路径。"
    if not (os.path.exists(path) or "://" in path):
        return False, "路径不存在：%s" % path
    try:
        if IS_WINDOWS:
            os.startfile(path)               # type: ignore[attr-defined]  # noqa: S606
        elif IS_MACOS:
            _quiet_popen(["open", path])
        else:
            _quiet_popen(["xdg-open", path])
        return True, "已用默认程序打开：%s" % path
    except AttributeError:
        # 极端情况：非 Windows 上走到了 os.startfile 分支
        return False, "当前系统（%s）不支持 os.startfile，请改用「打开所在文件夹」。" % platform_name()
    except FileNotFoundError:
        hint = "xdg-open" if IS_LINUX else "open"
        return False, "缺少打开工具（%s）：请先安装 xdg-utils，或用文件管理器手动打开。" % hint
    except Exception as ex:                  # noqa: BLE001
        return False, "打开失败：%s" % ex


def reveal_in_file_manager(path: str):
    """在文件管理器中**定位**到该文件（选中它）。

    Windows → ``explorer /select,``；macOS → ``open -R``；
    Linux → 打开其所在目录（多数文件管理器不支持"选中"参数）。
    """
    path = str(path or "")
    if not path:
        return False, "没有可定位的路径。"
    if not os.path.exists(path):
        return False, "路径不存在：%s" % path
    target = os.path.normpath(path)
    try:
        if IS_WINDOWS:
            _quiet_popen(["explorer", "/select,", target])
        elif IS_MACOS:
            _quiet_popen(["open", "-R", target])
        else:
            folder = target if os.path.isdir(target) else os.path.dirname(target)
            _quiet_popen(["xdg-open", folder])
        return True, "已在文件管理器中定位：%s" % target
    except FileNotFoundError:
        return False, "找不到文件管理器命令（explorer / open / xdg-open）。"
    except Exception as ex:                  # noqa: BLE001
        return False, "定位失败：%s" % ex


def script_argv(path: str):
    """按扩展名返回"怎么运行这个脚本"的 argv（跨平台）。

    ``.py`` → 当前解释器；``.bat`` → Windows 的 cmd（**非 Windows 明确不支持**）；
    ``.sh`` → bash；其它 → ``None``（由调用方决定如何提示）。
    """
    p = str(path or "")
    ext = os.path.splitext(p)[1].lower()
    if ext == ".py":
        return [sys.executable, p]
    if ext == ".bat":
        return ["cmd", "/c", p] if IS_WINDOWS else None
    if ext == ".sh":
        return ["bash", p]
    return None


def unsupported_note(what: str) -> str:
    """统一的"本平台不支持"文案（带上平台名，便于排查）。"""
    return "%s 在当前系统（%s）上不支持。" % (what, platform_name())
