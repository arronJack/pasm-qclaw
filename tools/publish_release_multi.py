# -*- coding: utf-8 -*-
"""publish_release_multi —— 双端（Gitee + GitHub）建 Release 并上传**多份**附件。

与 publish_release.py 的差别：
  · 后者只发一个 `PASMStudio-Setup-<ver>.exe`；本脚本支持**每端一组附件**
    （v0.31.1 起桌面端是三平台同发，Gitee 侧还要分卷）。
  · 支持 `--split-gitee`：把超 100MB 的文件切成 <100MB 的分卷
    （Gitee 对单个附件有 100MB 硬上限）。
  · Release 已存在时**不报错**，改为复用已有 release 继续传附件（重试友好）。

用法
----
    # 预演（只打印要发什么，不发请求）
    python tools/publish_release_multi.py --ver 0.31.1 \
        --title-gitee "v0.31.1 · 三平台同发" --title-github "v0.31.1 · tri-platform" \
        --body-file release_body_v0311.md \
        --asset-gitee installer/PASMStudio-Setup-0.31.1-gitee.exe \
        --asset-gitee nix/PASMStudio-0.31.1-linux-x86_64.tar.gz \
        --split-gitee  --dry-run

    # 正式发
    python tools/publish_release_multi.py --ver 0.31.1 ...（同上，去掉 --dry-run）

坑位备忘（沿用既有经验）：
  - Gitee 建 Release 必须带 target_commitish；附件字段名是 **file**（multipart）
  - Gitee 附件列表接口是 /releases/{id}/attach_files（这个才返回带 id 的条目）
  - GitHub 走 upload_url（去掉 {?name,label} 后缀）+ `?name=` 查询参数
  - 路径一律不写死（tools/pasm_paths.py 定位）
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
BRANCH = "master"
SPLIT_SIZE = 95 * 1000 * 1000          # 95MB：留足余量（Gitee 上限 100MB）


def http(url, method="POST", data=None, headers=None, raw=None, ctype=None, timeout=1800):
    h = dict(headers or {})
    if raw is not None:
        data = raw
        h.setdefault("Content-Type", ctype or "application/octet-stream")
    elif data is not None:
        data = json.dumps(data).encode()
        h.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode()
            return r.status, (json.loads(body) if body.strip().startswith(("{", "[")) else body)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:600]


def human(n):
    return "%.1f MB" % (n / 1048576.0)


def split_file(path, size=SPLIT_SIZE):
    """切成 <size 的分卷：path.001 / .002 …（用 `cat path.0* > path` 合并）。"""
    parts = []
    base = os.path.basename(path)
    full = os.path.getsize(path)
    n = (full + size - 1) // size
    print("    分卷: %s (%s) -> %d 卷" % (base, human(full), n))
    with open(path, "rb") as f:
        for i in range(1, n + 1):
            part = "%s.%03d" % (path, i)
            with open(part, "wb") as out:
                left = min(size, full - (i - 1) * size)
                while left > 0:
                    chunk = f.read(min(1 << 20, left))
                    if not chunk:
                        break
                    out.write(chunk)
                    left -= len(chunk)
            parts.append(part)
            print("      %-58s %s" % (os.path.basename(part), human(os.path.getsize(part))))
    return parts


def gitee_asset(token, rid, path):
    name = os.path.basename(path)
    with open(path, "rb") as f:
        boundary = "----pb" + os.urandom(8).hex()
        pre = ("--%s\r\nContent-Disposition: form-data; name=\"file\"; filename=\"%s\"\r\n"
               "Content-Type: application/octet-stream\r\n\r\n" % (boundary, name)).encode()
        raw = pre + f.read() + ("\r\n--%s--\r\n" % boundary).encode()
        return http("https://gitee.com/api/v5/repos/%s/releases/%d/attach_files" % (GITEE_REPO, rid),
                    headers={"Authorization": "token " + token},
                    raw=raw, ctype="multipart/form-data; boundary=" + boundary)


def github_asset(headers, up, path):
    with open(path, "rb") as f:
        return http(up + "?name=" + os.path.basename(path), headers=headers,
                    raw=f.read(), ctype="application/octet-stream")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ver", required=True, help="如 0.31.1（不带 v）")
    ap.add_argument("--title-gitee", required=True)
    ap.add_argument("--title-github", required=True)
    ap.add_argument("--body-file", required=True)
    ap.add_argument("--asset-gitee", action="append", default=[], help="可重复")
    ap.add_argument("--asset-github", action="append", default=[], help="可重复")
    ap.add_argument("--split-gitee", action="store_true",
                    help="Gitee 侧 >100MB 的附件自动分卷（<name>.001/.002…）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", choices=["gitee", "github", "both"], default="both")
    a = ap.parse_args()

    tag = "v" + a.ver
    here = os.path.abspath(__file__)
    body = open(a.body_file, encoding="utf-8").read() if os.path.isfile(a.body_file) else None
    if body is None:
        raise SystemExit("Release 说明文件不存在：%s" % a.body_file)

    print("=" * 74)
    print("发行仓 : %s" % (pasm_paths.find_deploy_dir(here) or "(未找到)"))
    print("tag    : %s" % tag)
    print("说明   : %s (%d 字节)" % (a.body_file, len(body.encode())))
    print()

    # 校验附件存在
    for label, lst in (("Gitee", a.asset_gitee), ("GitHub", a.asset_github)):
        print("%s 附件（%d 个）:" % (label, len(lst)))
        for p in lst:
            ok = os.path.isfile(p)
            print("   %s %-58s %s" % ("v" if ok else "x", os.path.basename(p),
                                      human(os.path.getsize(p)) if ok else "**不存在**"))
            if not ok and not a.dry_run:
                raise SystemExit("附件不存在：%s" % p)
        print()

    if a.dry_run:
        if a.split_gitee:
            print("将分卷（>%s）:" % human(SPLIT_SIZE))
            for p in a.asset_gitee:
                if os.path.isfile(p) and os.path.getsize(p) > SPLIT_SIZE:
                    n = (os.path.getsize(p) + SPLIT_SIZE - 1) // SPLIT_SIZE
                    print("   %-58s -> %d 卷" % (os.path.basename(p), n))
        print("dry-run：未发起任何请求")
        return 0

    ok = True

    # ---------------- Gitee ----------------
    if a.only in ("gitee", "both"):
        gt = pasm_paths.read_token("gitee", "Gitee 令牌", here)
        st, rel = http("https://gitee.com/api/v5/repos/%s/releases" % GITEE_REPO,
                       data={"access_token": gt, "tag_name": tag, "name": a.title_gitee,
                             "body": body, "target_commitish": BRANCH, "prerelease": False})
        rid = None
        if isinstance(rel, dict) and rel.get("id"):
            rid = rel["id"]
            print("Gitee create release:", st, "id:", rid)
        else:
            # 已存在 → 复用
            print("Gitee create release:", st, str(rel)[:200])
            st2, rel2 = http("https://gitee.com/api/v5/repos/%s/releases/tags/%s"
                             % (GITEE_REPO, tag), method="GET",
                             headers={"Authorization": "token " + gt})
            if isinstance(rel2, dict) and rel2.get("id"):
                rid = rel2["id"]
                print("Gitee 复用已有 release id:", rid)
            else:
                print("Gitee 取已有 release 失败:", st2, str(rel2)[:200])
                ok = False

        if rid:
            assets = list(a.asset_gitee)
            if a.split_gitee:
                for p in list(assets):
                    if os.path.getsize(p) > SPLIT_SIZE:
                        assets.remove(p)
                        assets += split_file(p)
            for p in assets:
                st3, r3 = gitee_asset(gt, rid, p)
                print("   upload %-58s -> %s %s" % (os.path.basename(p), st3,
                                                    "" if st3 < 300 else str(r3)[:200]))
                ok &= st3 < 300
        print()

    # ---------------- GitHub ----------------
    if a.only in ("github", "both"):
        gh = pasm_paths.read_token("github", "GitHub 令牌", here)
        hh = {"Authorization": "token " + gh, "Accept": "application/vnd.github+json"}
        st, grel = http("https://api.github.com/repos/%s/releases" % GITHUB_REPO,
                        headers=hh,
                        data={"tag_name": tag, "name": a.title_github, "body": body,
                              "target_commitish": BRANCH, "prerelease": False})
        up = None
        if isinstance(grel, dict) and grel.get("upload_url"):
            up = grel["upload_url"].split("{")[0]
            print("GitHub create release:", st)
        else:
            print("GitHub create release:", st, str(grel)[:200])
            st2, g2 = http("https://api.github.com/repos/%s/releases/tags/%s"
                           % (GITHUB_REPO, tag), method="GET", headers=hh)
            if isinstance(g2, dict) and g2.get("upload_url"):
                up = g2["upload_url"].split("{")[0]
                print("GitHub 复用已有 release")
            else:
                print("GitHub 取已有 release 失败:", st2, str(g2)[:200])
                ok = False
        if up:
            for p in a.asset_github:
                st3, r3 = github_asset(hh, up, p)
                print("   upload %-58s -> %s %s" % (os.path.basename(p), st3,
                                                    "" if st3 < 300 else str(r3)[:200]))
                ok &= st3 < 300

    print()
    print("DONE" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
