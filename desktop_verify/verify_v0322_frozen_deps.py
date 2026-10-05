# -*- coding: utf-8 -*-
"""verify_v0322_frozen_deps.py —— 冻结版「运行时依赖完整性」守卫。

背景（2026-10-05 一天内连踩两次同类坑）：
  ① 重建构建 venv 时只装构建工具 → 漏装 aiohttp/numpy/pydantic
     → dist 比上版少 78 文件 / 41MB，装好后连接器等能力静默失效。
  ② 漏装 **openai**（云端 SDK）→ 装好后所有开发任务报
     `RuntimeError: openai 库未安装`，云端模型完全调不起来。

两次都靠"和上一版 dist 逐文件 diff"才抓到，但那是事后。
本守卫把关键依赖**变成显式契约**：任一缺失即红，且能在打包前就发现
（venv 缺包 → 红色；spec/build_common 未声明 → 红色）。

跑法：
    python desktop_verify/verify_v0322_frozen_deps.py            # 只查声明
    python desktop_verify/verify_v0322_frozen_deps.py --dist DIR # 查某个 dist 的 PYZ
    python desktop_verify/verify_v0322_frozen_deps.py --check-venv
"""
import argparse
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "desktop"))
sys.path.insert(0, ROOT)

FAILS = []
CNT = [0]


def check(name, cond, extra=""):
    if not isinstance(name, str):
        raise TypeError("check() 第一个参数必须是 str（收到 %r）" % type(name))
    CNT[0] += 1
    print(("  [OK]   " if cond else "  [FAIL] ") + name
          + (("  " + str(extra)) if (extra and not cond) else ""))
    if not cond:
        FAILS.append(name)


#: 冻结版必须具备的运行时依赖（key -> 用途说明）
REQUIRED = {
    "openai":   "云端 LLM SDK（DeepSeek/OpenAI 等，0.31.22 曾漏装导致云端全废）",
    "httpx":    "openai 3.x 的 HTTP 后端",
    "httpcore": "httpx 的底层连接池",
    "anyio":    "异步 IO（openai 依赖）",
    "aiohttp":  "连接器/WebSocket 通道",
    "pydantic": "多模块的数据模型校验",
    "numpy":    "pasm 部分认知模块与部分技能",
    "PySide6":  "GUI 框架（含 QtWebEngine）",
    "PyInstaller": "不打包，仅确认构建工具存在（非运行时）",
}
#: 只在构建 venv 需要、不进 dist 的构建期工具
BUILD_ONLY = {"PyInstaller"}


def _pythons():
    yield "构建 venv", r"E:/AI/pasm/code/.buildvenv_0322/Scripts/python.exe"
    yield "系统 python", sys.executable


def check_declared():
    print("=== 1. spec / build_common 声明检查（防漏打包）===")
    spec = os.path.join(ROOT, "PASMStudio.spec")
    src = open(spec, encoding="utf-8").read()
    try:
        import build_common as B
        imports = set(B.HIDDEN_IMPORTS)
    except Exception as ex:
        imports = set()
        check("build_common 可导入", False, ex)

    for mod, why in REQUIRED.items():
        if mod in BUILD_ONLY:
            continue
        # openai 依赖链按需声明
        need = [mod]
        if mod == "openai":
            need += ["httpx", "httpcore", "anyio", "sniffio", "distro", "tqdm"]
        miss_bc = [m for m in need if m not in imports]
        check("build_common 声明 %s" % ",".join(need), not miss_bc,
              "缺: %s —— %s" % (miss_bc, why))
        miss_spec = [m for m in need if "'%s'" % m not in src]
        check("PASMStudio.spec 声明 %s" % ",".join(need), not miss_spec,
              "缺: %s" % miss_spec)


def check_venv():
    print("\n=== 2. 构建 venv 依赖检查（防漏装）===")
    py = r"E:/AI/pasm/code/.buildvenv_0322/Scripts/python.exe"
    if not os.path.isfile(py):
        check("构建 venv 存在", False, py)
        return
    code = ("import importlib,json\n"
            "out={}\n"
            "for m in %r:\n"
            "    try: importlib.import_module(m); out[m]=True\n"
            "    except Exception: out[m]=False\n"
            "print(json.dumps(out))" % list(REQUIRED))
    try:
        r = subprocess.run([py, "-c", code], capture_output=True, text=True, timeout=180)
        import json
        got = json.loads(r.stdout.strip().splitlines()[-1])
    except Exception as ex:
        check("venv 依赖可探测", False, ex)
        return
    for mod, why in REQUIRED.items():
        check("venv 已装 %s" % mod, got.get(mod) is True, why)


def check_dist(dist):
    print("\n=== 3. dist 冻结产物 PYZ 校验（%s）===" % dist)
    import glob
    pyzs = glob.glob(os.path.join(dist, "**", "PYZ-00.pyz"), recursive=True)
    if not pyzs:
        check("找到 PYZ-00.pyz", False, dist)
        return
    p = pyzs[0]
    print("  PYZ: %s (%.1f MB)" % (p, os.path.getsize(p) / 1e6))
    code = (
        "from PyInstaller.archive.readers import ZlibArchiveReader\n"
        "z=ZlibArchiveReader(%r)\n"
        "n=set(z.toc)\n"
        "import json\n"
        "print(json.dumps({m: (m in n) for m in %r}))" % (p, list(REQUIRED))
    )
    try:
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=300)
        import json
        got = json.loads(r.stdout.strip().splitlines()[-1])
    except Exception as ex:
        check("PYZ TOC 可读", False, ex)
        return
    for mod, why in REQUIRED.items():
        if mod in BUILD_ONLY:
            continue
        check("PYZ 收录 %s" % mod, got.get(mod) is True, why)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dist", default=None, help="要校验的 dist 目录")
    ap.add_argument("--check-venv", action="store_true", help="同时检查构建 venv")
    ap.add_argument("--skip-venv", action="store_true")
    a = ap.parse_args()

    print("=== verify_v0322 frozen deps ===")
    check_declared()
    if not a.skip_venv:
        check_venv()
    if a.dist:
        check_dist(a.dist)

    print("\n=== 结果：%d 项断言，失败 %d ===" % (CNT[0], len(FAILS)))
    if FAILS:
        print("FAILED: %s" % FAILS)
        return 1
    print("ALL GREEN")
    return 0


if __name__ == "__main__":
    sys.exit(main())
