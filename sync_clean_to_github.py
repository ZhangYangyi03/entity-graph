#!/usr/bin/env python
"""sync_clean_to_github.py

After a data-cleaning run lands its artefacts, commit and push ONCE.

Usage:
  python sync_clean_to_github.py --once              # sync now, then exit
  python sync_clean_to_github.py --watch            # stay resident: one push per clean
  python sync_clean_to_github.py --once --dry-run    # show what would happen

A "clean" is judged finished when the data files stop changing for --settle
seconds. Repeated runs with no change do nothing (no empty commits, no pushes).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Files produced by cleaning / graph building. Everything else is ignored here;
# .gitignore still protects *.sqlite, *.log, .env, .cache/, out/.
DATA_GLOBS = (
    "*.graph.json", "web*.json", "*_pool.json", "*_cache.json",
    "shards/*.json", "*.graph.jsonl", "*.ndjson",
    "WEB_COVERAGE.md", "COMPANIES.md", "NOTES.md", "README.md",
    "sync_state.json",
)
# Never stage anything that smells like a secret.
DENY = (".env", "token", "secret", "credential", "private_key", "id_rsa", "hosts.yml")


def run(args, cwd, env=None):
    p = subprocess.run(args, cwd=cwd, capture_output=True, timeout=180,
                       env={**os.environ, **(env or {}), "GIT_PAGER": "cat", "PAGER": "cat"})
    out = (p.stdout or b"").decode("utf-8", "replace").strip()
    err = (p.stderr or b"").decode("utf-8", "replace").strip()
    return p.returncode, out, err


def git(cwd, *args):
    return run(["git", "--no-pager"] + list(args), cwd)


def utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(repo, line):
    with open(repo / "sync.log", "a", encoding="utf-8") as fh:
        fh.write(f"{utc()} {line}\n")
    # pythonw has no console: sys.stdout is None there. Never print from the watcher.
    try:
        sys.stdout.write(f"{utc()} {line}\n")
        sys.stdout.flush()
    except Exception:
        pass


def fingerprint(repo):
    """mtime + size of every data artefact; a cleaning run changes this."""
    items = []
    for pat in DATA_GLOBS:
        for p in sorted(repo.glob(pat)):
            if p.is_file():
                st = p.stat()
                items.append((str(p.relative_to(repo)), st.st_size, st.st_mtime_ns))
    return hashlib.sha256(json.dumps(items).encode()).hexdigest(), len(items)


def suspicious(repo):
    hits = []
    for p in repo.rglob("*"):
        if not p.is_file() or ".git" in p.parts or "__pycache__" in p.parts:
            continue
        n = p.name.lower()
        if any(d in n for d in DENY) and p.stat().st_size < 200_000:
            hits.append(str(p.relative_to(repo)))
    return hits


def changed(repo):
    rc, out, _ = git(repo, "status", "--porcelain")
    return [l for l in out.splitlines() if l.strip()] if rc == 0 else []


def sync(repo, remote, branch, dry, message=None):
    lines = changed(repo)
    if not lines:
        return "clean (nothing to sync)"
    bad = suspicious(repo)
    # Only care if a suspicious file is actually among the staged candidates.
    stage_bad = [b for b in bad if any(b in l for l in lines)]
    if stage_bad:
        return f"REFUSED: secret-looking files would be staged: {stage_bad[:5]}"
    if dry:
        return f"DRY-RUN would commit {len(lines)} changed path(s): {lines[:6]}"

    rc, out, err = git(repo, "add", "-A")
    if rc != 0:
        return f"FAIL git add: {err[:200]}"
    rc, out, err = git(repo, "diff", "--cached", "--stat")
    stat = out.replace("\n", " ") if rc == 0 else ""
    msg = message or f"data: auto-sync cleaned artefacts {utc()} ({len(lines)} paths)"
    rc, out, err = git(repo, "commit", "-m", msg)
    if rc != 0:
        return f"FAIL git commit: {err[:200]}"
    # Read the new commit id from the raw object, not from a decorated ref:
    # on this host `git show/log` on a Chinese-message commit can come back empty.
    rc, sha, err = git(repo, "rev-parse", "HEAD")
    sha = sha[:7] if rc == 0 else "?"
    rc, out, err = git(repo, "push", remote, f"HEAD:{branch}")
    if rc != 0:
        return f"COMMITTED {sha} locally but PUSH FAILED: {err[:300]} (will retry next round)"
    # Keep the local branch ref aligned with what we just pushed.
    cur, _, _ = git(repo, "branch", "--show-current")
    if cur and cur != branch:
        git(repo, "branch", "-f", branch, "HEAD")
    return f"PUSHED {sha} -> {remote}/{branch} | {stat[:160]} | {msg}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=str(Path(__file__).resolve().parent))
    ap.add_argument("--remote", default="origin")
    ap.add_argument("--branch", default="main")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--interval", type=float, default=3.0)
    ap.add_argument("--settle", type=float, default=8.0,
                    help="seconds of no file change before a clean is called done")
    ap.add_argument("--message")
    a = ap.parse_args()
    repo = Path(a.repo).resolve()
    if not (repo / ".git").exists():
        print(f"not a git repo: {repo}", file=sys.stderr)
        return 2
    if a.once or not a.watch:
        print(sync(repo, a.remote, a.branch, a.dry_run, a.message))
        return 0

    log(repo, f"WATCH start repo={repo} settle={a.settle}s interval={a.interval}s pid={os.getpid()}")
    fp, n = fingerprint(repo)
    stable_since = None
    last_synced_head, _, _ = git(repo, "rev-parse", "HEAD")
    while True:
        time.sleep(a.interval)
        try:
            now_fp, n = fingerprint(repo)
            if now_fp != fp:
                fp, stable_since = now_fp, time.time()
                continue
            if stable_since is None or time.time() - stable_since < a.settle:
                continue
            head, _, _ = git(repo, "rev-parse", "HEAD")
            if head == last_synced_head and not changed(repo):
                stable_since = None
                continue
            res = sync(repo, a.remote, a.branch, False, a.message)
            log(repo, f"SYNC {n} data files | {res}")
            if res.startswith("PUSHED") or res.startswith("clean"):
                last_synced_head, _, _ = git(repo, "rev-parse", "HEAD")
            stable_since = None
        except KeyboardInterrupt:
            log(repo, "WATCH stopped by user")
            return 0
        except Exception as e:
            log(repo, f"WATCH error: {type(e).__name__}: {e}")


if __name__ == "__main__":
    sys.exit(main())
