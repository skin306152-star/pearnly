# -*- coding: utf-8 -*-
"""存档截图：每张单存好后留一张，只存在这台电脑，满 30 天自动删除（主会话 10-26），不进原件层、不上传。

- 每个批次一个目录，目录里有一份清单 ``_batch.json``（批次号、批次开始日期、本批写过的截图文件名）；
- 到期按清单里的批次日期判断，不按文件修改时间（拷贝、杀毒、备份都会改修改时间）；
- 只删清单里列着的、本程序写的文件，目录里别的东西一律不碰，删空了才删目录；没有清单的目录不碰；
- 清理时机：每次批次开始前、结束后各扫一次，CC 启动时也扫一次（``prune``）；不开常驻进程、不定时轮询；
- 删掉后汇报里这张的「回看」显示已过期（``state`` 回 expired），不报错。
"""
from __future__ import annotations

import base64
import datetime as dt
import json
import re
import time
from pathlib import Path
from typing import Dict, List

KEEP_DAYS = 30
MANIFEST = "_batch.json"
KEPT, EXPIRED, NONE = "kept", "expired", "none"


def _safe(name: str) -> str:
    return re.sub(r"[^\w.-]", "_", str(name))[:80] or "_"


def _read(folder: Path) -> Dict:
    try:
        data = json.loads((folder / MANIFEST).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


class Evidence:
    def __init__(self, root: Path, batch_id: str, *, batch_date: str = "", now=time.time):
        """``batch_date``：批次开始日期（ISO，空 = 今天）。"""
        self.root = Path(root)
        self.batch_id = str(batch_id)
        self.dir = self.root / _safe(batch_id)
        self.now = now
        self.batch_date = batch_date or dt.date.fromtimestamp(now()).isoformat()

    def _manifest(self) -> Dict:
        data = _read(self.dir)
        return data or {"batch": self.batch_id, "date": self.batch_date, "files": {}}

    def save(self, doc_id: str, png_b64: str) -> Path:
        self.dir.mkdir(parents=True, exist_ok=True)
        manifest = self._manifest()
        name = f"{_safe(doc_id)}.png"
        (self.dir / name).write_bytes(base64.b64decode(png_b64))
        manifest["files"][str(doc_id)] = name
        tmp = self.dir / (MANIFEST + ".tmp")
        tmp.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.dir / MANIFEST)
        return self.dir / name

    def path(self, doc_id: str) -> Path:
        return self.dir / f"{_safe(doc_id)}.png"

    def state(self, doc_id: str) -> str:
        """回看用：kept = 还在；expired = 写过、已到期删掉；none = 这张没有存档截图。"""
        if self.path(doc_id).is_file():
            return KEPT
        return EXPIRED if str(doc_id) in _read(self.dir).get("files", {}) else NONE

    def drop(self) -> None:
        """删批次：连同截图一起真删（只删清单里列着的文件）。"""
        _remove(self.dir, _read(self.dir), keep_manifest=False)


def expired(batch_date: str, now: float) -> bool:
    try:
        day = dt.date.fromisoformat(str(batch_date)[:10])
    except ValueError:
        return False  # 清单里没有可认的日期：判不出，不删
    return (dt.date.fromtimestamp(now) - day).days >= KEEP_DAYS


def _remove(folder: Path, manifest: Dict, *, keep_manifest: bool) -> int:
    removed = 0
    for name in manifest.get("files", {}).values():
        target = folder / _safe(Path(str(name)).name)
        if target.is_file():
            target.unlink()
            removed += 1
    if keep_manifest and manifest:
        # 留下清单（记着已过期），回看时显示「已过期」；目录里只剩清单
        manifest = dict(manifest, expired=True)
        (folder / MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    else:
        (folder / MANIFEST).unlink(missing_ok=True)
        if folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()
    return removed


def prune(root: Path, *, now=time.time) -> List[str]:
    """扫一遍代录截图目录，删满 30 天的批次截图。回删过截图的批次号。"""
    root = Path(root)
    done: List[str] = []
    if not root.is_dir():
        return done
    for folder in root.iterdir():
        if not folder.is_dir():
            continue
        manifest = _read(folder)
        if not manifest or manifest.get("expired") or not expired(manifest.get("date", ""), now()):
            continue
        if _remove(folder, manifest, keep_manifest=True):
            done.append(str(manifest.get("batch", folder.name)))
    return done

