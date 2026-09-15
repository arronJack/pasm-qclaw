# -*- coding: utf-8 -*-
"""publish_release —— 双端（Gitee + GitHub）建 Release 并上传安装包附件。

用法（在仓库根或任意目录）：
    python tools/publish_release.py --ver 0.30.2 \
        --title-gitee "v0.30.2 · 聊天与数据目录修复" \
        --title-github "v0.30.2 · chat & data-dir fixes" \
        --body-file release_body_v0302.md

    # 只想先看看它会用哪个安装包、哪个发行仓，不真的发
    python tools/publish_release.py --ver 0.30.2 --dry-run \
        --title-gitee x --title-github x --body-file x.md

坑位备忘：
  - Gitee 建 Release 必须带 target_commitish；附件字段名是 file（multipart）
  - 路径一律不写死（定位规则见 tools/pasm_paths.py），否则换机器第一步就断

安装包定位顺序：
    --exe 显式指定 → PASM_RELEASE_DIR → 候选目录（含 PASM/installer、生态根）
    → 命中文件名 PASMStudio-Setup-<ver>.exe
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pasm_paths                                            # noqa: E402

GITEE_REPO = "arronzheng/pasm-qclaw"
GITHUB_REPO = "arronJack/pasm-qclaw"


def find_installer(ver, explicit=""):
    """找安装包：显式路径 → 发行仓 → 候选目录（含 PASM/installer 与生态根）。"""
    name = "PASMStudio-Setup-%s.exe" % ver
    if explicit:
        return os.path.abspath(explicit)

    here = os.path.abspath(__file__)
    dirs = []
    rel = pasm_paths.find_deploy_dir(here)
    if rel:
        dirs.append(rel)
    for base in pasm_paths.candidate_dirs(here):
        dirs += [base,
                 os.path.join(base, "installer"),
                 os.path.join(base, "PASM", "installer"),
                 os.path.join(base, "release")]
    for d in pasm_paths.candidate_dirs(here):
        for n in pasm_paths.REL_DIR_NAMES:
            dirs.append(os.path.join(d, n, "installer"))
    for d in dirs:
        p = os.path.join(d, name)
        if os.path.isfile(p):
            return p
    return os.path.join(rel or os.getcwd(), name)            # 交给调用方报错


def http(url, method="POST", data=None, headers=None, raw=None, ctype=None):
    h = dict(headers or {})
    if raw is not None:
        data = raw
        h.setdefault("Content-Type", ctype or "application/octet-stream")
    elif data is not None:
        data = json.dumps(data).encode()
        h.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            body = r.read().decode()
            return r.status, (json.loads(body) if body.strip().startswith(("{", "[")) else body)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:500]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ver", required=True, help="如 0.30.2（不带 v）")
    ap.add_argument("--title-gitee", required=True)
    ap.add_argument("--title-github", required=True)
    ap.add_argument("--body-file", required=True, help="Release 说明 Markdown 文件路径")
    ap.add_argument("--exe", default="", help="安装包路径（默认自动搜索）")
    ap.add_argument("--dry-run", action="store_true", help="只打印将要使用的路径，不发起请求")
    a = ap.parse_args()

    here = os.path.abspath(__file__)
    tag = "v" + a.ver
    exe = find_installer(a.ver, a.exe)
    exe_name = os.path.basename(exe)
    rel_dir = pasm_paths.find_deploy_dir(here)

    print("发行仓 :", rel_dir or "（未找到，仅按 --exe 指定的安装包发）")
    print("安装包 :", exe)
    if not os.path.isfile(exe):
        raise SystemExit("安装包不存在：%s\n（用 --exe 指定，或把安装包放进发行仓/installer 目录）" % exe)
    if not os.path.isfile(a.body_file):
        raise SystemExit("Release 说明文件不存在：%s" % a.body_file)
    print("说明文档:", a.body_file, "(%d 字节)" % os.path.getsize(a.body_file))

    if a.dry_run:
        gt = pasm_paths.read_token("gitee", "Gitee 令牌", here, required=False)
        gh = pasm_paths.read_token("github", "GitHub 令牌", here, required=False)
        print("Gitee 令牌 :", "已找到（%d 字符）" % len(gt) if gt else "**未找到**")
        print("GitHub 令牌:", "已找到（%d 字符）" % len(gh) if gh else "**未找到**")
        print("dry-run：未发起任何请求")
        return 0

    body = open(a.body_file, encoding="utf-8").read()
    branch = "master"
    ok = True

    # ---------- Gitee ----------
    gt = pasm_paths.read_token("gitee", "Gitee 令牌", here)
    st, rel = http("https://gitee.com/api/v5/repos/%s/releases" % GITEE_REPO,
                   data={"access_token": gt, "tag_name": tag,
                         "name": a.title_gitee, "body": body,
                         "target_commitish": branch, "prerelease": False})
    print("Gitee create release:", st)
    if isinstance(rel, dict):
        rid = rel["id"]
        print("  release id:", rid)
        with open(exe, "rb") as f:
            boundary = "----pb" + os.urandom(8).hex()
            pre = ("--%s\r\nContent-Disposition: form-data; name=\"file\"; filename=\"%s\"\r\n"
                   "Content-Type: application/octet-stream\r\n\r\n" % (boundary, exe_name)).encode()
            raw = pre + f.read() + ("\r\n--%s--\r\n" % boundary).encode()
            st2, r2 = http("https://gitee.com/api/v5/repos/%s/releases/%d/attach_files"
                           % (GITEE_REPO, rid),
                           headers={"Authorization": "token " + gt},
                           raw=raw, ctype="multipart/form-data; boundary=" + boundary)
        print("Gitee upload asset:", st2, "" if st2 < 300 else str(r2)[:300])
        ok &= st2 < 300
    else:
        print("  resp:", rel)
        ok = False

    # ---------- GitHub ----------
    gh_token = pasm_paths.read_token("github", "GitHub 令牌", here)
    hh = {"Authorization": "token " + gh_token, "Accept": "application/vnd.github+json"}
    st, grel = http("https://api.github.com/repos/%s/releases" % GITHUB_REPO,
                    headers=hh,
                    data={"tag_name": tag, "name": a.title_github,
                          "body": body, "target_commitish": branch, "prerelease": False})
    print("GitHub create release:", st)
    if isinstance(grel, dict):
        up = grel["upload_url"].split("{")[0]
        with open(exe, "rb") as f:
            st3, r3 = http(up + "?name=" + exe_name, headers=hh,
                           raw=f.read(), ctype="application/octet-stream")
        print("GitHub upload asset:", st3, "" if st3 < 300 else str(r3)[:300])
        ok &= st3 < 300
    else:
        print("  resp:", grel)
        ok = False

    print("DONE" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
