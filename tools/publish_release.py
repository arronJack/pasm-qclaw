# -*- coding: utf-8 -*-
"""publish_release —— 双端（Gitee + GitHub）建 Release 并上传安装包附件。

用法（在仓库根或任意目录）：
    python tools/publish_release.py --ver 0.28.3 \
        --title-gitee "v0.28.3 · 真机三修" \
        --title-github "v0.28.3 · Three real-device fixes" \
        --body-file /tmp/release_body.md

约定（对齐发行惯例）：
  - 附件 = E:/AI/pasm_qclaw_release/PASMStudio-Setup-<ver>.exe
  - Gitee：arronzheng/pasm-qclaw，令牌文件 E:/AI/7天有效期的gitee私人令牌.txt
  - GitHub：arronJack/pasm-qclaw，令牌文件 E:/AI/github_token.txt
  - 坑位备忘：Gitee 建 Release 必须带 target_commitish；附件字段名是 file（multipart）
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

GITEE_REPO = "arronzheng/pasm-qclaw"
GITHUB_REPO = "arronJack/pasm-qclaw"
REL_DIR = r"E:\AI\pasm_qclaw_release"


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
        with urllib.request.urlopen(req) as r:
            body = r.read().decode()
            return r.status, (json.loads(body) if body.strip().startswith(("{", "[")) else body)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:500]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ver", required=True, help="如 0.28.3（不带 v）")
    ap.add_argument("--title-gitee", required=True)
    ap.add_argument("--title-github", required=True)
    ap.add_argument("--body-file", required=True, help="Release 说明 Markdown 文件路径")
    a = ap.parse_args()

    tag = "v" + a.ver
    exe = os.path.join(REL_DIR, "PASMStudio-Setup-%s.exe" % a.ver)
    exe_name = os.path.basename(exe)
    body = open(a.body_file, encoding="utf-8").read()
    branch = "master"
    ok = True

    # ---------- Gitee ----------
    gt = open(r"E:\AI\7天有效期的gitee私人令牌.txt").read().strip()
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
    gh_token = open(r"E:\AI\github_token.txt").read().strip()
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
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
