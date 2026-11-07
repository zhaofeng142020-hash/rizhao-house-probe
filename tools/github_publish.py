"""把本仓库发布到 GitHub（只走 api.github.com，不依赖 github.com）。

为什么需要它：某些网络环境里 github.com 被代理拦截，`git push` 和 `gh` 都用不了，
但 api.github.com 可达。这里改用 GitHub 的 Git Data API：逐个文件建 blob →
建一棵 tree → 建一个 commit → 把 refs/heads/main 指过去，最终得到一个干净的初始提交。
空仓库会先用 Contents API 点亮（GitHub 不允许在零提交仓库里建 blob），引导提交随后被覆盖。

认证：只从环境变量 GITHUB_TOKEN 读一个 classic PAT（需要 repo 权限）。
脚本不会把令牌写进任何文件、不会打印它、也不会把它塞进提交内容。
用完请在 GitHub 设置里撤销该 PAT。

用法：
    GITHUB_TOKEN=ghp_xxx python tools/github_publish.py
    GITHUB_TOKEN=ghp_xxx python tools/github_publish.py --repo my-name --private

仓库归属自动取 PAT 对应的账号（GET /user），无需手工填用户名。
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

API = "https://api.github.com"
ROOT = Path(__file__).resolve().parent.parent
UA = "rizhao-house-probe-publisher"


def call(method: str, path: str, token: str, payload: dict | None = None,
         timeout: int = 90) -> tuple[int, dict]:
    url = path if path.startswith("http") else API + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", UA)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"message": raw[:300]}
    except Exception as exc:  # noqa: BLE001
        return 0, {"message": f"{type(exc).__name__}: {exc}"}


def die(msg: str) -> None:
    print("[失败]", msg)
    raise SystemExit(1)


def tracked_files() -> list[str]:
    out = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT)
    return [p for p in out.decode("utf-8").split("\0") if p]


def ensure_repo_initialized(base: str, token: str) -> None:
    """空仓库不能用 Git Data API 建 blob（409 Git Repository is empty）。

    先用 Contents API 落一个最小提交把仓库「点亮」；稍后我们会把 main
    强制指向自己构造的、无 parent 的提交，这个引导提交不会留在可见历史里。
    """
    code, refs = call("GET", base + "/git/refs/heads", token)
    if code == 200 and isinstance(refs, list) and refs:
        return
    code, out = call("PUT", base + "/contents/README.md", token, {
        "message": "Initialize repository",
        "content": base64.b64encode(b"# Rizhao House Probe\n").decode("ascii"),
    })
    if code not in (200, 201):
        die(f"初始化空仓库失败 HTTP {code}: {out.get('message', out)}")
    print("空仓库已初始化（引导提交稍后会被最终提交替换）")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default="rizhao-house-probe")
    ap.add_argument("--private", action="store_true", help="建私有仓库（默认公开）")
    ap.add_argument("--message", default="Initial open-source release: 日照房价探针 / 捡漏雷达")
    ap.add_argument("--description",
                    default="日照房价探针 / 捡漏雷达：真实房源监测、异动提醒与购房成本测算")
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        die("未设置 GITHUB_TOKEN。请传入一个有 repo 权限的 classic PAT（用完请撤销）。")

    code, me = call("GET", "/user", token)
    if code != 200 or not me.get("login"):
        die(f"令牌校验失败 HTTP {code}: {me.get('message', me)}")
    owner = me["login"]
    print(f"已认证：{owner}")

    files = tracked_files()
    if not files:
        die("git 里没有已跟踪文件，请先 git add && git commit")
    print(f"待推送文件：{len(files)} 个（均为 git 已跟踪内容，自动遵守 .gitignore）")

    # 1) 建仓库（已存在则复用）
    code, repo = call("GET", f"/repos/{owner}/{args.repo}", token)
    if code == 200:
        print(f"复用已有仓库：{repo['html_url']}")
    elif code == 404:
        code, repo = call("POST", "/user/repos", token, {
            "name": args.repo,
            "description": args.description,
            "private": args.private,
            "has_issues": True,
            "has_projects": False,
            "has_wiki": False,
            # 必须 auto_init：GitHub 不允许在「完全没有提交」的仓库里创建
            # blob（409 Git Repository is empty）。初始提交稍后会被覆盖。
            "auto_init": True,
        })
        if code != 201:
            die(f"创建仓库失败 HTTP {code}: {repo.get('message', repo)}")
        print(f"仓库已创建：{repo['html_url']}")
    else:
        die(f"查询仓库失败 HTTP {code}: {repo.get('message', repo)}")

    base = f"/repos/{owner}/{args.repo}"

    # 1.5) 复用一个建到一半的空仓库时，先把它点亮
    ensure_repo_initialized(base, token)

    # 2) 为每个文件建 blob（二进制统一用 base64）
    def make_blob(rel: str) -> tuple[str, int, str | None, str]:
        raw = (ROOT / rel).read_bytes()
        payload = {
            "content": base64.b64encode(raw).decode("ascii"),
            "encoding": "base64",
        }
        # 大批量创建对象容易撞上 GitHub 的 secondary rate limit，
        # 对可重试的状态做几次退避重试，避免整轮白跑。
        for attempt in range(4):
            code, out = call("POST", base + "/git/blobs", token, payload)
            if code == 201 and out.get("sha"):
                return rel, code, out["sha"], ""
            msg = str(out.get("message", ""))
            if code in (0, 403, 409, 429, 502, 503):
                time.sleep(1.5 * (attempt + 1))
                continue
            return rel, code, None, msg
        return rel, code, None, msg

    entries: list[dict] = []
    failures: list[str] = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        for rel, code, sha, msg in pool.map(make_blob, files):
            if code == 201 and sha:
                entries.append({"path": rel, "mode": "100644", "type": "blob", "sha": sha})
            else:
                failures.append(f"{rel}: HTTP {code} {msg[:120]}")
    if failures:
        for f in failures[:10]:
            print("  blob 失败:", f)
        die(f"{len(failures)} 个文件未能上传，未创建任何提交")
    print(f"blob 完成：{len(entries)} 个")

    # 3) tree → commit → ref，做成一次提交
    code, tree = call("POST", base + "/git/trees", token, {"tree": entries})
    if code != 201:
        die(f"创建 tree 失败 HTTP {code}: {tree.get('message', tree)}")

    code, commit = call("POST", base + "/git/commits", token, {
        "message": args.message,
        "tree": tree["sha"],
        "parents": [],
    })
    if code != 201:
        die(f"创建 commit 失败 HTTP {code}: {commit.get('message', commit)}")

    # 引导提交 / auto_init 建的分支名取决于账号默认设置（main 或 master），
    # 统一收敛到 main，并把引导提交挤出可见历史。
    code, meta = call("GET", base, token)
    default = meta.get("default_branch", "main") if code == 200 else "main"

    code, ref = call("POST", base + "/git/refs", token, {
        "ref": "refs/heads/main", "sha": commit["sha"],
    })
    if code != 201:
        # main 已存在则强制更新（仅用于重推自己的新仓库）
        code2, ref2 = call("PATCH", base + "/git/refs/heads/main", token,
                           {"sha": commit["sha"], "force": True})
        if code2 != 200:
            die(f"写入 main 失败 HTTP {code}/{code2}: {ref.get('message', ref)}")

    # 设 main 为默认分支；若引导用的是别的分支名，删掉那个空分支
    call("PATCH", base, token, {"default_branch": "main"})
    if default and default != "main":
        call("DELETE", base + "/git/refs/heads/" + default, token)

    print("\n[完成]")
    print("  仓库：", f"https://github.com/{owner}/{args.repo}")
    print("  可见性：", "private" if args.private else "public")
    print("  提交：", commit["sha"][:12], f"（{len(entries)} 个文件，单一初始提交）")
    print("  提醒：用完请到 GitHub Settings → Developer settings 撤销该 PAT。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
