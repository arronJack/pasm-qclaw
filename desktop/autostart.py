"""autostart —— 开机自动启动（v0.29 新增）。

实现方式：写 HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run。
选它的理由：
    * 只写当前用户（HKEY_CURRENT_USER）→ **不需要管理员提权**
    * 与「启动」文件夹等价，但可查询、可幂等写入、便于程序内开关
    * 比计划任务轻量，且卸载时容易清理

命令行两种形态（路径含空格必须加引号，否则开机时会启动失败且没有任何提示）：
    * 打包态：  "C:\\...\\PASMStudio.exe"
    * 源码态：  "C:\\...\\pythonw.exe" "C:\\...\\desktop\\pasm_main.py"
      用 pythonw 而不是 python，避免开机弹出一个黑色控制台窗口。

所有接口都返回 (ok: bool, msg: str)，方便 UI 直接提示，不抛异常。
非 Windows 平台优雅降级为「不支持」，调用方不必特判。
"""
from __future__ import annotations

import os
import sys

APP_KEY = "PASMStudio"
APP_LABEL = "PASM Studio"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def is_supported() -> bool:
    return os.name == "nt"


def _main_script() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in ("pasm_main.py", "pasm_companion.py"):
        p = os.path.join(here, cand)
        if os.path.isfile(p):
            return p
    return os.path.join(here, "pasm_main.py")


def _python_w() -> str:
    """源码态优先用 pythonw（无控制台窗口）；找不到就退回当前解释器。"""
    exe = sys.executable or ""
    d, base = os.path.dirname(exe), os.path.basename(exe).lower()
    if base.startswith("python") and not base.startswith("pythonw"):
        cand = os.path.join(d, "pythonw.exe")
        if os.path.isfile(cand):
            return cand
    return exe


def launch_command() -> str:
    """返回开机应执行的完整命令行（已按需加引号）。"""
    if getattr(sys, "frozen", False):          # PyInstaller 打包态
        return '"%s"' % sys.executable
    return '"%s" "%s"' % (_python_w(), _main_script())


def is_enabled() -> bool:
    if not is_supported():
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            val, _ = winreg.QueryValueEx(k, APP_KEY)
            return bool(str(val).strip())
    except FileNotFoundError:
        return False
    except OSError:
        return False
    except Exception:
        return False


def current_value() -> str:
    if not is_supported():
        return ""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            val, _ = winreg.QueryValueEx(k, APP_KEY)
            return str(val)
    except Exception:
        return ""


def enable() -> tuple:
    """开启开机自启。幂等：重复调用只会覆盖成同一条正确命令行。"""
    if not is_supported():
        return False, "当前系统不支持（仅 Windows 可用）"
    cmd = launch_command()
    if not cmd or len(cmd) < 8:
        return False, "启动命令生成异常，已取消"
    try:
        import winreg
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                                winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, APP_KEY, 0, winreg.REG_SZ, cmd)
        # 回读校验（不信写入自身，按本项目惯例以回读为准）
        got = current_value()
        if got != cmd:
            return False, "写入后回读不一致，请检查权限"
        return True, "已开启开机自启"
    except Exception as ex:
        return False, "开启失败：%s" % ex


def disable() -> tuple:
    """关闭开机自启。不存在也算成功（幂等）。"""
    if not is_supported():
        return False, "当前系统不支持（仅 Windows 可用）"
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as k:
            try:
                winreg.DeleteValue(k, APP_KEY)
            except FileNotFoundError:
                pass
        if is_enabled():
            return False, "删除后仍检测到该项，请手动检查注册表"
        return True, "已关闭开机自启"
    except FileNotFoundError:
        return True, "已关闭开机自启"
    except Exception as ex:
        return False, "关闭失败：%s" % ex


def set_enabled(on: bool) -> tuple:
    return enable() if on else disable()


def status() -> dict:
    """给设置界面/自检用的一份状态快照。"""
    return {
        "supported": is_supported(),
        "enabled": is_enabled(),
        "registered": current_value(),
        "expected": launch_command(),
        "matches": is_enabled() and current_value() == launch_command(),
    }


def _selftest() -> int:
    """自检：用独立键名测试，绝不触碰真实的 PASMStudio 项。

    为什么用独立键名：自检会在真机上写注册表，若直接用正式键名，
    跑一次自检就会**偷偷给用户打开开机自启** —— 那是不可接受的副作用。
    """
    global APP_KEY
    if not is_supported():
        print("SKIP 非 Windows，跳过（接口已优雅降级）")
        return 0
    import winreg
    real = APP_KEY
    APP_KEY = "PASMStudio__selftest"
    ok_all, n = True, 0
    try:
        disable()
        n += 1
        r1 = is_enabled() is False
        ok_all &= r1
        print(("PASS " if r1 else "FAIL ") + "初始态为未开启")

        ok, msg = enable()
        n += 1
        ok_all &= ok
        print(("PASS " if ok else "FAIL ") + "enable() -> " + msg)

        r2 = is_enabled() is True
        n += 1
        ok_all &= r2
        print(("PASS " if r2 else "FAIL ") + "is_enabled() 为真")

        first = current_value()
        enable()                                    # 幂等：再来一次
        r3 = current_value() == first
        n += 1
        ok_all &= r3
        print(("PASS " if r3 else "FAIL ") + "重复 enable 幂等（未产生重复项）")

        r4 = first == launch_command()
        n += 1
        ok_all &= r4
        print(("PASS " if r4 else "FAIL ") + "命令行与 launch_command() 一致")

        r5 = first.count('"') in (2, 4)
        n += 1
        ok_all &= r5
        print(("PASS " if r5 else "FAIL ") + "路径已正确加引号: " + first)

        ok2, msg2 = disable()
        n += 1
        ok_all &= ok2
        print(("PASS " if ok2 else "FAIL ") + "disable() -> " + msg2)

        r6 = is_enabled() is False
        n += 1
        ok_all &= r6
        print(("PASS " if r6 else "FAIL ") + "关闭后已清除")

        disable()
        r7 = is_enabled() is False
        n += 1
        ok_all &= r7
        print(("PASS " if r7 else "FAIL ") + "重复 disable 幂等")
    finally:
        try:
            disable()
        except Exception:
            pass
        APP_KEY = real
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                                winreg.KEY_SET_VALUE) as k:
                try:
                    winreg.DeleteValue(k, "PASMStudio__selftest")
                except FileNotFoundError:
                    pass
        except Exception:
            pass
    print("\n%d 项，%s" % (n, "全部通过" if ok_all else "存在失败"))
    print("（自检只使用临时键名 PASMStudio__selftest，已清理；未触碰真实的 %s 项）" % real)
    return 0 if ok_all else 1


if __name__ == "__main__":
    if sys.argv[1:2] == ["--selftest"]:
        sys.exit(_selftest())
    st = status()
    print("支持:", st["supported"], "| 已开启:", st["enabled"])
    print("当前注册值:", st["registered"] or "(无)")
    print("预期命令  :", st["expected"])
