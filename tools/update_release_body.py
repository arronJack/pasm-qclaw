#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""更新三端 Release 的**说明正文**（Gitee / GitHub / GitCode）。

为什么需要它：macOS / Linux 的产物是 CI 事后补挂的，补完得把「下载与安装」一节
改成带**真实文件名 + 大小**的版本；但 Release 正文早已发布，改本地
`release_body_vX.md` 不会同步到线上 —— 之前只能手工调 API（2026-09-30 之前一直缺这一步）。

用法：
    python tools/update_release_body.py --ver 0.31.19 --body-file release_body_v03119.md
    python tools/update_release_body.py --ver 0.31.19 --body-file release_body_v03119.md --only gitee
    ... --dry-run          # 只看会更新哪些，不发请求
"""
import argparse
import io
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, r"E:/AI/pasm/code/PASM/tools")
import pasm_paths                                                       # noqa: E402

GITEE_REPO = "arronzheng/pasm-qclaw"
GITHUB_REPO = "arronJack/pasm-qclaw"
GITCODE_REPO = "arronzheng/pasm-qclaw"
GITCODE_API = "https://api.gitcode.com/api/v5"


def _req(method, url, tok, *, bearer=False, gitee=True, data=None):
    h = {"Authorization": ("Bearer " if bearer else "token ") + tok}
    if not gitee:
        h["Accept"] = "application/vnd.github+json"
    if data is not None:
        h["Content-Type"] = "application/json"
    r = urllib.request.Request(url, method=method, headers=h,
                               data=json.dumps(data).encode() if data is not None else None)
    try:
        with urllib.request.urlopen(r, timeout=120) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:300]


def update_gitee(tok, tag, body, dry):
    st, b = _req("GET", "https://gitee.com/api/v5/repos/%s/releases/tags/%s"
                 % (GITEE_REPO, tag), tok)
    if st != 200:
        print("  [!] Gitee 查 Release 失败", st, b[:120])
        return False
    rid = json.loads(b).get("id")
    if dry:
        print("  [dry] Gitee 更新 release id=%s 正文（%d 字）" % (rid, len(body)))
        return True
    st2, b2 = _req("PATCH", "https://gitee.com/api/v5/repos/%s/releases/%s"
                   % (GITEE_REPO, rid), tok, data={"body": body})
    print("  Gitee PATCH -> %s" % st2)
    return st2 in (200, 201)


def update_github(tok, tag, body, dry):
    st, b = _req("GET", "https://api.github.com/repos/%s/releases/tags/%s"
                 % (GITHUB_REPO, tag), tok, gitee=False)
    if st != 200:
        print("  [!] GitHub 查 Release 失败", st, b[:120])
        return False
    rid = json.loads(b).get("id")
    if dry:
        print("  [dry] GitHub 更新 release id=%s 正文（%d 字）" % (rid, len(body)))
        return True
    st2, b2 = _req("PATCH", "https://api.github.com/repos/%s/releases/%s"
                   % (GITHUB_REPO, rid), tok, gitee=False, data={"body": body})
    print("  GitHub PATCH -> %s" % st2)
    return st2 == 200


def update_gitcode(tok, tag, body, dry):
    """GitCode 只能按 **tag** 更新，且 `name` 必填。

    ★ 实测（2026-09-30）：GitCode 的 `GET /releases/tags/{tag}` 与 `GET /releases`
      **都不返回 release id**（字段里根本没有 id）→ 按 id PATCH 走不通；
      `PATCH /releases/tags/{tag}` 报 `405 METHOD_NOT_ALLOWED`；
      只有 `PATCH /releases/{tag}` 可用，但缺 `name` 会报
      `400 PARAMETER_ERROR: must not be blank` —— **必须带上 name**。
    """
    st, b = _req("GET", "%s/repos/%s/releases/tags/%s" % (GITCODE_API, GITCODE_REPO, tag),
                 tok, gitee=False, bearer=True)
    if st != 200:
        print("  [!] GitCode 查 Release 失败", st, b[:120])
        return False
    name = (json.loads(b).get("name") or tag)
    if dry:
        print("  [dry] GitCode 更新 releases/%s 正文（%d 字，name=%r）" % (tag, len(body), name))
        return True
    st2, b2 = _req("PATCH", "%s/repos/%s/releases/%s" % (GITCODE_API, GITCODE_REPO, tag),
                   tok, gitee=False, bearer=True, data={"name": name, "body": body})
    print("  GitCode PATCH -> %s" % st2)
    return st2 in (200, 201)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ver", required=True, help="版本号，如 0.31.19")
    ap.add_argument("--body-file", required=True, help="正文 markdown 文件")
    ap.add_argument("--only", default="all", choices=["all", "gitee", "github", "gitcode"])
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    tag = "v" + a.ver
    body = io.open(a.body_file, encoding="utf-8").read()
    gt = pasm_paths.read_token("gitee", start_file=r"E:/AI/pasm/code/PASM/tools/publish_release.py",
                               required=False)
    ht = pasm_paths.read_token("github", start_file=r"E:/AI/pasm/code/PASM/tools/publish_release.py",
                               required=False)
    kt = pasm_paths.read_token("gitcode", start_file=r"E:/AI/pasm/code/PASM/tools/publish_release.py",
                               required=False)
    print("更新 %s 的 Release 正文（%s，%d 字）" % (tag, a.body_file, len(body)))
    ok = []
    if a.only in ("all", "gitee") and gt:
        ok.append(("Gitee", update_gitee(gt, tag, body, a.dry_run)))
    if a.only in ("all", "github") and ht:
        ok.append(("GitHub", update_github(ht, tag, body, a.dry_run)))
    if a.only in ("all", "gitcode") and kt:
        ok.append(("GitCode", update_gitcode(kt, tag, body, a.dry_run)))
    print("结果：" + "、".join("%s=%s" % (k, "OK" if v else "FAIL") for k, v in ok))
    return 0 if all(v for _k, v in ok) else 1


if __name__ == "__main__":
    sys.exit(main())
