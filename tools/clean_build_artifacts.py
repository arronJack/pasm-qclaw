#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""clean_build_artifacts —— 清理 PASM 仓里的构建产物与中间目录（v0.31.6 新增）。

背景（小志 2026-09-24）：一天内迭代了 v0.31.1~v0.31.6，仓里积了 13 个 `dist_*`
（每个 295~730MB）+ 12 个 `build_*`（每个 55MB），合计约 8.8GB，E 盘只剩 5.9GB。

**为什么单独写成脚本**：这类删除一次要动 3 万多个文件，AI 侧的安全删除护栏
（单次 >50 文件即拦截）不允许批量执行 —— 由用户自己跑一条命令最干净、最可控。

用法：
    python tools/clean_build_artifacts.py                 # 预演（只列，不删）
    python tools/clean_build_artifacts.py --yes           # 真删
    python tools/clean_build_artifacts.py --yes --keep dist_v0316d
    python tools/clean_build_artifacts.py --yes --keep-all-dist   # 只清 build_*

默认策略（安全侧）：
  · `build*`：全部清（PyInstaller 中间态，无保留价值）；
  · `dist*`：保留**版本号最大的那一个**（最新构建，供 diff/冒烟复用），其余清；
  · `installer/` 里的安装包**一律不动**（发布要用）；
  · 只删除**本仓根目录下**的目录，绝不递归扫其他路径。
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _version_key(name: str):
    """从 dist_v0.31.6 / dist_v0316d 这类名字里抽出可比较的版本元组（取数字段）。"""
    nums = []
    cur = ""
    for ch in name:
        if ch.isdigit() or ch == ".":
            cur += ch
        else:
            if cur:
                nums.extend(int(x) for x in cur.split(".") if x.isdigit())
                cur = ""
    if cur:
        nums.extend(int(x) for x in cur.split(".") if x.isdigit())
    return tuple(nums) or (0,)


def scan(keep: str = "", keep_all_dist: bool = False):
    """返回 (待删目录列表, 保留目录列表)。只在本仓根目录下找 dist*/build*。"""
    if not os.path.isdir(ROOT):
        return [], []
    dists, builds = [], []
    for nm in sorted(os.listdir(ROOT)):
        p = os.path.join(ROOT, nm)
        if not os.path.isdir(p):
            continue
        if nm.startswith("dist"):
            dists.append(nm)
        elif nm.startswith("build"):
            builds.append(nm)
    keep_names = set()
    if keep:
        keep_names.add(keep)
    elif dists and not keep_all_dist:
        # 默认保留"版本号最大"的那个（最新构建）
        keep_names.add(sorted(dists, key=_version_key)[-1])
    todo = [n for n in builds if n not in keep_names]
    todo += [n for n in dists if n not in keep_names]
    kept = [n for n in (builds + dists) if n in keep_names]
    return sorted(todo), sorted(kept)


def size_of(path: str) -> int:
    total = 0
    for r, _d, fs in os.walk(path):
        for f in fs:
            try:
                total += os.path.getsize(os.path.join(r, f))
            except Exception:
                pass
    return total


def main() -> int:
    global ROOT
    ap = argparse.ArgumentParser(description="清理 PASM 仓构建产物（dist_* / build_*）")
    ap.add_argument("--yes", action="store_true", help="真的删除（默认只预演）")
    ap.add_argument("--keep", default="", help="额外保留的目录名（如 dist_v0316d）")
    ap.add_argument("--keep-all-dist", action="store_true",
                    help="不删任何 dist*（只清 build*）")
    ap.add_argument("--root", default=ROOT, help="仓库根目录（默认本脚本上一级）")
    a = ap.parse_args()

    ROOT = os.path.abspath(a.root)
    todo, kept = scan(keep=a.keep, keep_all_dist=a.keep_all_dist)
    if not todo and not kept:
        print("没找到 dist*/build* 目录（仓库根：%s）" % ROOT)
        return 0
    print("仓库根：%s\n" % ROOT)
    total = 0
    print("将清理（%d 个）：" % len(todo))
    for nm in todo:
        s = size_of(os.path.join(ROOT, nm))
        total += s
        print("  %-22s %8.1f MB" % (nm, s / 1048576.0))
    print("\n保留（%d 个）：" % len(kept))
    for nm in kept:
        print("  %-22s %8.1f MB"
              % (nm, size_of(os.path.join(ROOT, nm)) / 1048576.0))
    print("\n合计可释放：%.2f GB" % (total / 1073741824.0))
    print("安装包（installer/）不在清理范围，发布用的包会保留。")

    if not a.yes:
        print("\n（预演模式：什么都没删。确认真删请加 --yes）")
        return 0
    if not todo:
        print("\n无可清理项。")
        return 0
    ok = fail = 0
    for nm in todo:
        p = os.path.join(ROOT, nm)
        # 双保险：只删本仓根下的直接子目录，且名字必须是 dist*/build*
        if os.path.dirname(p) != ROOT or not os.path.basename(p).startswith(("dist", "build")):
            print("  跳过（不在白名单）：%s" % p)
            continue
        try:
            shutil.rmtree(p)
            ok += 1
            print("  已删 %s" % nm)
        except Exception as ex:                              # noqa: BLE001
            fail += 1
            print("  失败 %s：%s" % (nm, ex))
    print("\n完成：成功 %d / 失败 %d" % (ok, fail))
    return 0 if not fail else 1


if __name__ == "__main__":
    raise SystemExit(main())
