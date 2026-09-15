# -*- coding: utf-8 -*-
"""pasm_paths —— 生态内「发行仓 / 令牌文件 / 解释器」的定位，全部动态推导。

为什么要有这个模块
------------------
2026-09-15 的教训：生态在**公司机（E:\\AI\\pasm）**与**家机（H:\\pasm）**
之间来回拷贝，而脚本里写死了绝对路径，于是每换一次机器就有一批工具坏掉：
  - `tools/publish_release.py` 写死 `E:\\AI\\pasm_qclaw_release` 与
    `E:\\AI\\7天有效期的gitee私人令牌.txt` → 发版第一步 FileNotFoundError，
    报错指向不存在的路径，看起来像"令牌丢了"，实际只是路径过期；
  - 令牌文件后来还改过名（`7天有效期的gitee私人令牌.txt` → `gitee私人令牌.txt`），
    写死单一路径的脚本直接失效。

所以定位一律遵守同一条规则：
    **环境变量优先 → 按候选顺序搜索 → 失败时报错写清"可以放哪 / 怎么设"**
绝不写死某个盘符或某个用户名下的目录。

候选目录从哪来（去重、保持优先级）
----------------------------------
    1. 环境变量 PASM_RELEASE_DIR / PASM_HOME / PASM_ROOT / PASM_CODE
    2. 从**调用方文件**逐级上溯：tools/ → 仓根 → 生态根 → 生态根父级，
       每级同时看自身与 `code/` 子目录（兼容 `H:\\pasm` 与 `H:\\pasm\\code` 两层）
    3. `~/AI`（普通家目录布局）
    4. `E:/AI`（历史遗留，仅兜底；缺失不影响）

本模块**无副作用**：import 不会去解析路径，也不会因为找不到东西就退出。
"""
from __future__ import annotations

import os
import sys

#: 发行仓可能使用的目录名（在候选目录下逐个试）
REL_DIR_NAMES = ("pasm-qclaw", "pasm_qclaw_release", "pasm-qclaw-release",
                 "pasm_qclaw", "release")

#: 发行仓的判定标志文件
REL_DIR_MARKER = "latest.json"

#: 令牌文件名候选（历史上改过名，所以列全；不含 SSH key 之类非令牌文件）
TOKEN_FILES = {
    "gitee": ("gitee私人令牌.txt", "7天有效期的gitee私人令牌.txt",
              "gitee_token.txt", "gitee-token.txt"),
    "github": ("github_token.txt", "github-token.txt",
               "github-pasm-token.txt", "github_pasm_token.txt"),
}

#: 令牌环境变量名
TOKEN_ENVS = {"gitee": "GITEE_TOKEN", "github": "GITHUB_TOKEN"}

#: 打包/运行用的 venv 目录名候选
VENV_NAMES = (".buildvenv", "venv", ".venv")


def candidate_dirs(start_file=None):
    """按优先级返回「可能放着发行仓或令牌文件」的目录列表（去重）。"""
    out = []

    def add(path):
        if not path:
            return
        try:
            p = os.path.abspath(os.path.expanduser(str(path)))
        except Exception:                                  # noqa: BLE001
            return
        if p not in out:
            out.append(p)

    for key in ("PASM_RELEASE_DIR", "PASM_HOME", "PASM_ROOT", "PASM_CODE"):
        add(os.environ.get(key))

    here = os.path.dirname(os.path.abspath(start_file or sys.argv[0]))
    up = here
    for _ in range(5):
        parent = os.path.dirname(up)
        if parent == up:
            break
        up = parent
        add(up)
        add(os.path.join(up, "code"))

    add(os.path.join(os.path.expanduser("~"), "AI"))
    add("E:/AI")                                           # 历史遗留，仅兜底
    return out


def find_release_dir(start_file=None, required=True):
    """定位发行仓目录（含 latest.json）。找不到且 required 时报错并列出可放位置。"""
    v = os.environ.get("PASM_RELEASE_DIR")
    if v and os.path.isdir(v):
        return os.path.abspath(v)

    dirs = candidate_dirs(start_file)
    for base in dirs:
        if os.path.isfile(os.path.join(base, REL_DIR_MARKER)):
            return base
    for base in dirs:
        for name in REL_DIR_NAMES:
            d = os.path.join(base, name)
            if os.path.isfile(os.path.join(d, REL_DIR_MARKER)):
                return d

    if not required:
        return None
    raise SystemExit(
        "找不到发行仓目录（需含 %s）。\n"
        "  办法一：设环境变量 PASM_RELEASE_DIR 指向它\n"
        "  办法二：把发行仓放在以下任一处\n  %s"
        % (REL_DIR_MARKER,
           "\n  ".join(os.path.join(b, n)
                       for b in dirs[:6] for n in REL_DIR_NAMES[:2])))


def read_token(kind, label=None, start_file=None, required=True):
    """读令牌：环境变量优先 → 候选目录 × 候选文件名。

    返回值**绝不打印**，避免把令牌写进日志。
    找不到且 required 时，报错列出所有搜过的路径与如何用环境变量替代。
    """
    if kind not in TOKEN_ENVS:
        raise ValueError("未知令牌类型 %r（可选：%s）" % (kind, ", ".join(TOKEN_ENVS)))
    label = label or kind
    env_name = TOKEN_ENVS[kind]

    v = os.environ.get(env_name)
    if v and v.strip():
        return v.strip()

    rel = find_release_dir(start_file, required=False)
    dirs = ([rel, os.path.dirname(rel)] if rel else []) + candidate_dirs(start_file)

    searched = []
    for d in dirs:
        for name in TOKEN_FILES[kind]:
            p = os.path.join(d, name)
            searched.append(p)
            try:
                if os.path.isfile(p):
                    with open(p, encoding="utf-8") as f:
                        t = f.read().strip()
                    if t:
                        return t
            except OSError:
                continue

    if not required:
        return ""
    raise SystemExit(
        "%s 读取失败：既没有环境变量 %s，也不在以下候选路径里：\n  %s\n"
        "请把令牌放到其中一处，或先设置环境变量 %s。"
        % (label, env_name, "\n  ".join(searched[:24]), env_name))


def find_venv_pythons(start_file=None):
    """返回仓附近存在的 venv 解释器路径列表（用于提示"该用哪个解释器跑"）。"""
    found = []
    roots = [os.path.dirname(os.path.abspath(start_file or sys.argv[0]))]
    for base in list(roots):
        up = base
        for _ in range(3):
            up = os.path.dirname(up)
            if up and up not in roots:
                roots.append(up)
    if os.environ.get("PASM_HOME"):
        roots.append(os.environ["PASM_HOME"])
    for base in roots:
        for name in VENV_NAMES:
            p = os.path.join(base, name, "Scripts", "python.exe")
            if os.path.isfile(p) and p not in found:
                found.append(p)
    return found


def find_deploy_dir(start_file=None):
    """定位桌面发行仓的部署目录（安装包与 latest.json 同目录）。

    与 find_release_dir 的区别：这里**允许只有一个候选**（比如发行仓还没建
    latest.json，但已经放了 exe），因此只要目录存在就返回。
    """
    v = os.environ.get("PASM_RELEASE_DIR")
    if v and os.path.isdir(v):
        return os.path.abspath(v)
    for base in candidate_dirs(start_file):
        if os.path.isfile(os.path.join(base, REL_DIR_MARKER)):
            return base
        for name in REL_DIR_NAMES:
            d = os.path.join(base, name)
            if os.path.isdir(d):
                return d
    return None
