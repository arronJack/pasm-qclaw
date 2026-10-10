# -*- coding: utf-8 -*-
"""单实例 + **唤醒已有窗口**（跨进程，Qt 自带，零第三方依赖）。

真机事故（小志 2026-09-30 18:26–18:29，日志里 11 次「日志启动」，界面反复弹
「PASM 小人已经在运行了」）：
入口 `pasm_main.py` 用 `pasm_pet._ensure_single_instance()`（QLockFile 锁 `pasmpet.lock`）
当**整个程序**的单实例门，锁被占时只 `QMessageBox.information(...)` 然后 `return 0`
—— 用户的感受是「点一次图标弹一次提示，主窗口就是不出来」（已有实例的窗口可能压在
别的窗口后面或收在托盘里，提示语说的"看右下角"他找不到）。

做法（本模块）：
  · 启动先 `QLocalSocket.connectToServer(NAME)`：
      - **连得上** = 已有实例在跑 → 发一句 `show` 让它把窗口带到前台 → 本进程**安静退出**
      - **连不上** = 我是第一个 → `QLocalServer.listen(NAME)`，之后收到的 `show`
        触发 `on_activate()`（把窗口 showNormal + activateWindow）
  · 比 QLockFile 更稳的一点：服务端 socket 残留由 `removeServer()` 兜底清理，
    不会因为上次异常退出留下"僵尸锁"而永远起不来。

`selftest` 可独立跑（不开 GUI）：起两个进程/两次实例化，验证第二个能唤醒第一个。
"""
from __future__ import annotations

import os
import sys

NAME = "PASMStudio.SingleInstance.v1"
_MSG_SHOW = b"show"
_MSG_ACK = b"ok"


def scoped_name(base: str = NAME, data_dir: str = "") -> str:
    """把单实例名**绑定到数据目录**（2026-10-10 修）。

    为什么必须绑定：名字原本是固定的全局串 ——
      · 用户**开着应用**时，任何"用隔离数据目录真启动 exe"的验证
        （`frozen_startup_smoke` / `verify_v2_switch` 冻结段）都会被真实实例
        顶掉：新进程连上旧进程 → 收到 ack → 安静退出 → 验证假红；
      · 源码 dev 环境（`.pasmstudio_dev`）与安装版（`%APPDATA%\\PASMStudio`）
        数据目录不同，却共用一把锁 → 后开的被前一个顶掉，用户看到的是
        "双击了没反应"。两者本是两套数据、两个实例，不该互踢。

    绑定后：**同一数据目录仍严格单实例**（第二次启动照样唤醒已有窗口），
    不同数据目录互不打扰。空 data_dir 时退回旧名，保证既有行为不变。
    """
    if not data_dir:
        try:
            import logsetup
            data_dir = logsetup.data_dir()
        except Exception:                                        # noqa: BLE001
            data_dir = ""
    if not data_dir:
        return base
    try:
        import hashlib
        digest = hashlib.sha1(
            os.path.normcase(os.path.abspath(data_dir)).encode("utf-8")
        ).hexdigest()[:10]
    except Exception:                                            # noqa: BLE001
        digest = str(abs(hash(data_dir)))[:10]
    return "%s.%s" % (base, digest)


class SingleInstance:
    """跨进程单实例 + 唤醒。

    用法（见 `pasm_main.py`）：
        si = SingleInstance(NAME, on_activate=lambda: bring_to_front())
        if not si.try_become_primary():
            return 0                      # 已有实例，已被唤醒，本进程退出
        ... 正常启动 ...
    """

    def __init__(self, name: str = NAME, on_activate=None):
        self.name = name
        self.on_activate = on_activate
        self._server = None
        self._sock = None

    # ---------------------------------------------------------------- 主入口
    def try_become_primary(self) -> bool:
        """返回 True = 我是主实例（继续启动）；False = 已有实例（已通知它唤醒，本进程应退出）。

        ★ 必须**等对方回 ack** 才认账（2026-09-30 真机教训）：
          升级场景下用户往往"旧版还开着就双击新版"。旧版**没有**这个监听（今天才加的），
          可能留下一个能连上、却永远不会响应 `show` 的通道/僵死进程 ——
          只看"连得上"就退出，用户看到的就是**双击新版毫无反应**（比弹提示更糟）。
          所以：连上 → 发 show → 等 ack；**拿到 ack 才退出，拿不到就接管**。
        """
        from PySide6.QtNetwork import QLocalServer, QLocalSocket
        # ① 先试着连已有实例
        s = QLocalSocket()
        s.connectToServer(self.name)
        if s.waitForConnected(400):
            got = b""
            try:
                s.write(_MSG_SHOW)
                s.flush()
                s.waitForBytesWritten(400)
                if s.waitForReadyRead(700):
                    got = bytes(s.readAll())
            except Exception:                                    # noqa: BLE001
                got = b""
            try:
                s.disconnectFromServer()
            except Exception:                                    # noqa: BLE001
                pass
            self._sock = s
            if got.strip() == _MSG_ACK:
                return False                 # 对方（新版）确认接管，本进程退出
            # 拿不到 ack：旧版本 / 僵死通道 → **接管**（宁可多开一个，也不能让用户打不开）
        # ② 成为主实例：清理可能的残留 socket 后监听
        try:
            QLocalServer.removeServer(self.name)                 # 上次异常退出的残留
        except Exception:                                        # noqa: BLE001
            pass
        srv = QLocalServer()
        if not srv.listen(self.name):
            # 极罕见：清理后仍监听失败 → 不阻塞启动（宁可多开一个，也别起不来）
            return True
        srv.newConnection.connect(self._on_conn)
        self._server = srv
        return True

    # ---------------------------------------------------------------- 内部
    def _on_conn(self):
        if self._server is None:
            return
        while self._server.hasPendingConnections():
            c = self._server.nextPendingConnection()
            try:
                c.waitForReadyRead(200)
                c.readAll()
                # ★ 回 ack：让对方确认"真的有人接管"（没 ack 它会自己接管 —— 见 try_become_primary）
                c.write(_MSG_ACK)
                c.flush()
                c.waitForBytesWritten(200)
            except Exception:                                    # noqa: BLE001
                pass
            try:
                c.disconnectFromServer()
            except Exception:                                    # noqa: BLE001
                pass
            self.activate()

    def activate(self):
        """把已有窗口带到前台（回调由调用方提供）。"""
        if callable(self.on_activate):
            try:
                self.on_activate()
            except Exception:                                    # noqa: BLE001
                pass

    def close(self):
        try:
            if self._server is not None:
                self._server.close()
        except Exception:                                        # noqa: BLE001
            pass


# ------------------------------------------------------------------ selftest
def selftest() -> int:
    """跨进程自检：父进程当主实例，子进程尝试唤醒 → 必须收到激活回调。"""
    import subprocess
    import time
    from PySide6.QtCore import QCoreApplication, QTimer

    app = QCoreApplication(sys.argv)
    got = {"n": 0}
    si = SingleInstance("PASMStudio.Selftest.v1", on_activate=lambda: got.__setitem__("n", got["n"] + 1))
    ok_first = si.try_become_primary()
    print("  [%s] 第一个实例成为主实例" % ("OK" if ok_first else "FAIL"))

    here = os.path.abspath(__file__)
    code = (
        "import sys, os; sys.path.insert(0, %r)\n"
        "from PySide6.QtCore import QCoreApplication\n"
        "from single_instance import SingleInstance\n"
        "app = QCoreApplication(sys.argv)\n"
        "si = SingleInstance('PASMStudio.Selftest.v1')\n"
        "sys.exit(0 if not si.try_become_primary() else 3)\n"
    ) % os.path.dirname(here)
    # ★ 必须**边等子进程边跑事件循环**：父进程若阻塞（subprocess.run），就收不到连接、
    #   回不了 ack，子进程会（正确地）判定"对方不响应"并接管 → 这条用例反而假红。
    p2 = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE)
    t0 = time.time()
    while p2.poll() is None and time.time() - t0 < 30:
        app.processEvents()
        time.sleep(0.05)
    try:
        p2.wait(timeout=10)
    except Exception:                                            # noqa: BLE001
        p2.kill()
    p = p2
    print("  [%s] 第二个实例检测到已有实例并退出（rc=%s）"
          % ("OK" if p.returncode == 0 else "FAIL", p.returncode))
    for _ in range(20):                                          # 等事件到达
        app.processEvents()
        if got["n"]:
            break
        time.sleep(0.05)
    print("  [%s] 主实例收到了唤醒信号（收到 %d 次）"
          % ("OK" if got["n"] >= 1 else "FAIL", got["n"]))

    # ③ ★ 模拟"旧版本 / 僵死通道"：连得上，但**永远不回 ack** → 必须接管（自己继续启动）
    from PySide6.QtNetwork import QLocalServer as _QLS
    _dumb_name = "PASMStudio.Selftest.Dumb"
    _QLS.removeServer(_dumb_name)
    dumb = _QLS()
    dumb_ok = dumb.listen(_dumb_name)
    si2 = SingleInstance(_dumb_name)
    took = si2.try_become_primary()
    print("  [%s] 对方不回 ack（旧版/僵死）→ 自己接管、继续启动（不会'双击没反应'）"
          % ("OK" if took else "FAIL"))
    try:
        si2.close()
    except Exception:                                            # noqa: BLE001
        pass
    try:
        dumb.close()
    except Exception:                                            # noqa: BLE001
        pass

    # ④ ★ 2026-10-10：名字**绑定数据目录** → 不同数据目录不该互踢
    #   （源码 dev 版与安装版、隔离目录验证都靠这条不假红）。
    n_same1 = scoped_name("T.v1", r"C:\x\PASMStudio")
    n_same2 = scoped_name("T.v1", r"C:\x\PASMStudio")
    n_other = scoped_name("T.v1", r"C:\y\PASMStudio")
    n_upper = scoped_name("T.v1", r"c:\X\PASMStudio")
    # 不传 data_dir → 解析**当前**数据目录（pasm_main 就靠这个），必须与显式传一致
    try:
        import logsetup
        _amb = logsetup.data_dir()
    except Exception:                                            # noqa: BLE001
        _amb = ""
    n_amb = scoped_name("T.v1")
    print("  [%s] 同一数据目录 → 同名（仍是单实例）"
          % ("OK" if n_same1 == n_same2 else "FAIL"))
    print("  [%s] 不同数据目录 → 不同名（互不打扰）"
          % ("OK" if n_same1 != n_other else "FAIL"))
    print("  [%s] 大小写不同的同目录 → 同名（Windows 不区分大小写）"
          % ("OK" if n_same1 == n_upper else "FAIL"))
    print("  [%s] 不传目录 → 解析当前数据目录（与显式传一致）"
          % ("OK" if n_amb == scoped_name("T.v1", _amb) else "FAIL"))

    si.close()
    ok = (ok_first and p.returncode == 0 and got["n"] >= 1 and took
          and n_same1 == n_same2 and n_same1 != n_other
          and n_same1 == n_upper and n_amb == scoped_name("T.v1", _amb))
    print("RESULT: %s" % ("PASS" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        sys.exit(selftest())
    print("用法：python single_instance.py selftest")
