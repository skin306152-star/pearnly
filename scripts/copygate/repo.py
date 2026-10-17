# -*- coding: utf-8 -*-
"""两种"树"：git 里的某个提交，和磁盘上的一个目录。抽取器只认 paths() / read() / blob_id()。

为什么不直接读磁盘：差异模式要拿"基线那一版"的文案来比，基线只在 git 里；当前那一版
默认是工作树（含未提交改动），提交钩子给的是暂存快照目录，`--head` 给的是某个提交。
三者用同一套接口，比较逻辑就只写一份。
"""

from __future__ import annotations

import hashlib
import os
import subprocess

# 只看这些目录：文案来源都在里面；别的目录（docs、website、tests……）不扫
ROOTS = (
    "apps/",
    "frontend/assistant/src/",
    "companion/src/",
    "infrastructure/keycloak/themes/",
    "scripts/i18n/",
)
SKIP_DIRS = {"node_modules", "__pycache__", ".git", "dist", "build", ".venv"}


class GitError(RuntimeError):
    pass


def git(repo: str, *args: str, binary: bool = False, stdin: bytes = None):
    proc = subprocess.run(
        ["git", "-C", repo, *args],
        input=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        raise GitError(proc.stderr.decode("utf-8", "replace").strip() or "git %s 失败" % " ".join(args))
    return proc.stdout if binary else proc.stdout.decode("utf-8", "replace")


def rev_parse(repo: str, rev: str) -> str:
    return git(repo, "rev-parse", "--verify", "--quiet", rev + "^{commit}").strip()


def _wanted(path: str) -> bool:
    if not path.startswith(ROOTS):
        return False
    return not any(part in SKIP_DIRS for part in path.split("/"))


def _blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


class GitTree:
    """某个提交里的文件（只读）。"""

    def __init__(self, repo: str, rev: str):
        self.repo = repo
        self.rev = rev_parse(repo, rev)
        self._index = None
        self._cache: dict = {}

    @property
    def label(self) -> str:
        return self.rev[:8]

    def _load_index(self) -> dict:
        if self._index is None:
            out = git(self.repo, "ls-tree", "-r", "-z", "--full-tree", self.rev)
            index = {}
            for record in out.split("\0"):
                if not record:
                    continue
                meta, _, path = record.partition("\t")
                parts = meta.split()
                if len(parts) == 3 and parts[1] == "blob" and _wanted(path):
                    index[path] = parts[2]
            self._index = index
        return self._index

    def paths(self) -> list:
        return sorted(self._load_index())

    def blob_id(self, path: str):
        return self._load_index().get(path)

    def prefetch(self, paths) -> None:
        """一次 cat-file --batch 读多份，比逐个 git show 快得多。"""
        index = self._load_index()
        want = [p for p in paths if p in index and p not in self._cache]
        if not want:
            return
        stdin = "".join(index[p] + "\n" for p in want).encode()
        out = git(self.repo, "cat-file", "--batch", binary=True, stdin=stdin)
        pos = 0
        for path in want:
            end = out.index(b"\n", pos)
            header = out[pos:end].split()
            pos = end + 1
            if len(header) < 3 or header[1] == b"missing":
                self._cache[path] = None
                continue
            size = int(header[2])
            self._cache[path] = out[pos:pos + size].decode("utf-8", "replace")
            pos += size + 1

    def read(self, path: str):
        if path not in self._cache:
            self.prefetch([path])
        return self._cache.get(path)


class WorkTree:
    """磁盘上的目录：工作树（含未提交改动），或提交钩子给的暂存快照。"""

    def __init__(self, root: str, label: str = "工作树"):
        self.root = os.path.abspath(root)
        self.label = label
        self._paths = None
        self._cache: dict = {}

    def paths(self) -> list:
        if self._paths is None:
            found = []
            for top in ROOTS:
                base = os.path.join(self.root, top)
                for current, dirs, files in os.walk(base):
                    dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
                    for name in files:
                        rel = os.path.relpath(os.path.join(current, name), self.root).replace(os.sep, "/")
                        if _wanted(rel):
                            found.append(rel)
            self._paths = sorted(found)
        return self._paths

    def _bytes(self, path: str):
        try:
            with open(os.path.join(self.root, path), "rb") as handle:
                return handle.read()
        except OSError:
            return None

    def blob_id(self, path: str):
        data = self._bytes(path)
        return None if data is None else _blob_sha(data)

    def prefetch(self, paths) -> None:
        return None

    def read(self, path: str):
        if path not in self._cache:
            data = self._bytes(path)
            self._cache[path] = None if data is None else data.decode("utf-8", "replace")
        return self._cache[path]
