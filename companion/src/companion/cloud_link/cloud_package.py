# -*- coding: utf-8 -*-
r"""桥把云端那份 CC 的安装文件放进隧道盘 ``cc\``（``docs/work/2026-11-16-express-cloud-install-command.md``「文件从哪里来」）。

用户在云端 PowerShell 打的那一行命令（:func:`install_command`）要隧道盘 ``cc\`` 里有三份文件：

- ``cloud_install.ps1``：随正式包打进来的数据文件（``packaging/companion.spec`` 的 datas 放在本模块旁边；源码运行时取
  ``companion/packaging/cloud_install.ps1``）；
- ``CC-Cloud.zip``：云端精简包。发布流水线（``.github/workflows/companion-release.yml``）跟正式安装包一起打、一起发，
  ``infrastructure/production/receive-companion.py`` 放在安装包旁边，并在 ``latest.json``（自动更新同一份清单，
  ``updater.DOWNLOAD_SITE + updater.LATEST_PATH``）多写一项 ``cloud: {version, url, sha256, size}``；
- ``cloud.json``：``{version, sha256, size}`` 再加 ``host`` = ``rdp.host(连接主机)``（与配对两侧的约定一致，``cloud_side.read_host``）。

本机缓存 ``%LOCALAPPDATA%\Pearnly\cloud-package\<版本>\``（``CC-Cloud.zip`` + ``cloud.json``）。缓存没有或 sha256 不对才按清单下载
（经 ``net.client``，同 ``updater.download_installer`` 跟随重定向），边下边算 sha256，大小或 sha256 不对就删掉、报 ``checksum``。
清单取不到（离线）时用缓存里校验得过的最新一版。

写进 ``cc\`` 时每份先写 ``.part`` 再改名；已存在且 sha256 相同就跳过；``cloud.json`` 最后写（它在 = 前两份已齐）。

只在需要时做：``tomo_bridge`` 在 ``cloud_pair_begin`` 时调 :func:`prepare`（第一次配对前），点「连接」后只在 ``cc\cloud.json``
已经在、而清单里的版本变了时调 :func:`refresh`（``cc\`` 是空的就什么都不做，不联网）。空闲时不做任何事，不起线程、不定时。
本模块只在这两处按需导入。

失败抛 :class:`PackageError`，``code`` 是状态码（``no_package`` / ``download_failed`` / ``checksum`` / ``write_failed``），桥照原样
交给设置页；日志只有版本与状态码，不含令牌、密码。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Callable, Optional

from ..logger import get_logger
from . import rdp

log = get_logger(__name__)

CC_DIR = "cc"                      # 同 cloud_side.CC_DIR
MANIFEST = "cloud.json"            # 同 cloud_side.MANIFEST
PACKAGE = "CC-Cloud.zip"
SCRIPT = "cloud_install.ps1"
PART = ".part"
CHUNK = 262144
MAX_BYTES = 300 * 1024 * 1024

NO_PACKAGE = "no_package"
DOWNLOAD_FAILED = "download_failed"
CHECKSUM = "checksum"
WRITE_FAILED = "write_failed"
CODES = (NO_PACKAGE, DOWNLOAD_FAILED, CHECKSUM, WRITE_FAILED)

_VERSION = re.compile(r"^\d{1,4}(\.\d{1,4}){2,3}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class PackageError(RuntimeError):
    """准备 ``cc\\`` 失败；``code`` 是状态码。文本不带令牌、密码。"""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


# ── 清单 ─────────────────────────────────────────────────────────────────────

def parse_entry(data: Any) -> Optional[dict]:
    """``latest.json`` 里的 ``cloud`` 项 → ``{version, url, sha256, size}``；没有或不合规回 None。"""
    entry = data.get("cloud") if isinstance(data, dict) else None
    if not isinstance(entry, dict):
        return None
    version = str(entry.get("version") or "").strip()
    url = str(entry.get("url") or "").strip()
    sha256 = str(entry.get("sha256") or "").strip().lower()
    size = entry.get("size")
    if not _VERSION.match(version) or not _SHA256.match(sha256):
        return None
    if not isinstance(size, int) or isinstance(size, bool) or not 0 < size <= MAX_BYTES:
        return None
    if not (url.startswith("/downloads/companion/") or url.startswith("https://")) or len(url) > 500:
        return None
    return {"version": version, "url": url, "sha256": sha256, "size": size}


def _default_site() -> str:
    from ..updater import DOWNLOAD_SITE

    return DOWNLOAD_SITE


def _client(timeout: float):
    from .. import http_tls, net

    return net.client(timeout=timeout, follow_redirects=True, verify=http_tls.context())


def fetch_entry(site: Optional[str] = None, *, client: Callable[[float], Any] = _client,
                timeout: float = 15.0) -> Optional[dict]:
    """取发布清单里的云端包；取不到（离线、清单没有这一项）回 None。"""
    from ..updater import LATEST_PATH

    url = (site or _default_site()).rstrip("/") + LATEST_PATH
    try:
        with client(timeout) as c:
            r = c.get(url)
            r.raise_for_status()
            return parse_entry(r.json())
    except Exception as exc:  # noqa: BLE001 - 离线时用缓存
        log.info("取云端包清单失败：%s", type(exc).__name__)
        return None


# ── 本机缓存 ─────────────────────────────────────────────────────────────────

def _default_cache() -> Path:
    from ..config import CLOUD_PACKAGE_DIR

    return CLOUD_PACKAGE_DIR


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> Optional[dict]:
    try:
        with open(path, "rb") as handle:
            data = json.loads(handle.read(64 * 1024).decode("utf-8-sig"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _valid_cached(folder: Path, entry: Optional[dict] = None) -> Optional[dict]:
    """缓存里这一版齐全且 zip 的大小、sha256 对得上（给了 ``entry`` 还要与它一致）就回 ``{version, sha256, size}``。"""
    meta = _read_json(folder / MANIFEST)
    zip_path = folder / PACKAGE
    if meta is None or not zip_path.is_file():
        return None
    version, sha256, size = str(meta.get("version") or ""), str(meta.get("sha256") or ""), meta.get("size")
    if not _VERSION.match(version) or not _SHA256.match(sha256) or not isinstance(size, int):
        return None
    if entry is not None and (version != entry["version"] or sha256 != entry["sha256"]):
        return None
    try:
        if zip_path.stat().st_size != size or sha256_of(zip_path) != sha256:
            return None
    except OSError:
        return None
    return {"version": version, "sha256": sha256, "size": size}


def _version_key(name: str) -> tuple:
    return tuple(int(part) for part in name.split("."))


def newest_cached(cache: Path) -> Optional[tuple]:
    """离线兜底：缓存里校验得过的最新一版 ``(目录, {version, sha256, size})``；没有回 None。"""
    try:
        names = [entry.name for entry in Path(cache).iterdir() if entry.is_dir() and _VERSION.match(entry.name)]
    except OSError:
        return None
    for name in sorted(names, key=_version_key, reverse=True):
        meta = _valid_cached(Path(cache) / name)
        if meta is not None:
            return Path(cache) / name, meta
    return None


def _remove(path: Path) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError as exc:
        log.info("删除临时文件失败：%s", type(exc).__name__)


def download(entry: dict, folder: Path, *, site: Optional[str] = None, client: Callable[[float], Any] = _client,
             timeout: float = 600.0) -> dict:
    """按清单下载到 ``folder``：先写 ``CC-Cloud.zip.part``，大小与 sha256 都对才改名并写 ``cloud.json``；不对删掉报 ``checksum``。"""
    folder = Path(folder)
    url = entry["url"]
    full = url if url.startswith("https://") else (site or _default_site()).rstrip("/") + url
    part = folder / (PACKAGE + PART)
    digest, size, write_error = hashlib.sha256(), 0, False
    try:
        folder.mkdir(parents=True, exist_ok=True)
        handle = open(part, "wb")
    except OSError as exc:
        log.warning("写云端包缓存失败：%s", type(exc).__name__)
        raise PackageError(WRITE_FAILED) from None
    try:
        with handle, client(timeout) as c, c.stream("GET", full) as r:
            r.raise_for_status()
            for chunk in r.iter_bytes(chunk_size=CHUNK):
                size += len(chunk)
                if size > entry["size"]:
                    break
                digest.update(chunk)
                try:
                    handle.write(chunk)
                except OSError:
                    write_error = True
                    raise
    except Exception as exc:  # noqa: BLE001 - 网络错误一律算下载失败，写盘错误算写不进
        _remove(part)
        log.warning("下载云端包失败 · 版本=%s：%s", entry["version"], type(exc).__name__)
        raise PackageError(WRITE_FAILED if write_error else DOWNLOAD_FAILED) from None
    if size != entry["size"] or digest.hexdigest() != entry["sha256"]:
        _remove(part)
        log.warning("云端包校验不对，已删除 · 版本=%s", entry["version"])
        raise PackageError(CHECKSUM)
    meta = {"version": entry["version"], "sha256": entry["sha256"], "size": entry["size"]}
    try:
        os.replace(part, folder / PACKAGE)
        _write_atomic(folder / MANIFEST, _json_bytes(meta))
    except OSError as exc:
        _remove(part)
        log.warning("写云端包缓存失败：%s", type(exc).__name__)
        raise PackageError(WRITE_FAILED) from None
    log.info("云端包已下载 · 版本=%s", entry["version"])
    return meta


def ensure_cached(*, entry: Optional[dict], cache: Optional[Path] = None, site: Optional[str] = None,
                  client: Callable[[float], Any] = _client) -> tuple:
    """要用的那一版 ``(目录, {version, sha256, size})``：清单那一版缓存对得上就用，对不上就下载；没有清单用缓存最新一版。"""
    cache = Path(cache) if cache is not None else _default_cache()
    if entry is None:
        found = newest_cached(cache)
        if found is None:
            raise PackageError(NO_PACKAGE)
        return found
    folder = cache / entry["version"]
    meta = _valid_cached(folder, entry)
    if meta is None:
        meta = download(entry, folder, site=site, client=client)
    return folder, meta


# ── 隧道盘 cc\ ───────────────────────────────────────────────────────────────

def script_path() -> Optional[Path]:
    """安装脚本：打包后在本模块旁边（companion.spec datas），源码运行时在 ``companion/packaging/``。"""
    here = Path(__file__).resolve().parent
    for candidate in (here / SCRIPT, here.parents[2] / "packaging" / SCRIPT):
        if candidate.is_file():
            return candidate
    return None


def _json_bytes(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=True, indent=2) + "\n").encode("ascii")


def _write_atomic(target: Path, data: bytes) -> None:
    part = target.with_name(target.name + PART)
    try:
        with open(part, "wb") as handle:
            handle.write(data)
        os.replace(part, target)
    except OSError:
        _remove(part)
        raise


def _copy_atomic(source: Path, target: Path) -> None:
    part = target.with_name(target.name + PART)
    try:
        with open(source, "rb") as src, open(part, "wb") as dst:
            for chunk in iter(lambda: src.read(CHUNK), b""):
                dst.write(chunk)
        os.replace(part, target)
    except OSError:
        _remove(part)
        raise


def _same(target: Path, sha256: str) -> bool:
    try:
        return target.is_file() and sha256_of(target) == sha256
    except OSError:
        return False


def tunnel_manifest(meta: dict, host: str) -> dict:
    """``cc\\cloud.json``：发布的 ``{version, sha256, size}`` 加 ``host``（``rdp.host`` 规范化）。"""
    return {"version": meta["version"], "sha256": meta["sha256"], "size": meta["size"], "host": rdp.host(host)}


def place(root: Path, host: str, folder: Path, meta: dict, *, script: Optional[Path] = None) -> dict:
    """把三份文件写进 ``<隧道根>\\cc\\``；返回 ``{version, written: [...]}``（sha256 相同跳过的不在 written 里）。"""
    script = script if script is not None else script_path()
    if script is None:
        log.warning("找不到云端安装脚本")
        raise PackageError(NO_PACKAGE)
    target_dir = Path(root) / CC_DIR
    written = []
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        script_bytes = Path(script).read_bytes()
        manifest = _json_bytes(tunnel_manifest(meta, host))
        files = [
            (target_dir / SCRIPT, hashlib.sha256(script_bytes).hexdigest(), lambda t: _write_atomic(t, script_bytes)),
            (target_dir / PACKAGE, meta["sha256"], lambda t: _copy_atomic(Path(folder) / PACKAGE, t)),
            (target_dir / MANIFEST, hashlib.sha256(manifest).hexdigest(), lambda t: _write_atomic(t, manifest)),
        ]
        for target, sha256, write in files:
            if _same(target, sha256):
                continue
            write(target)
            written.append(target.name)
    except OSError as exc:
        log.warning("写隧道盘 cc 目录失败：%s", type(exc).__name__)
        raise PackageError(WRITE_FAILED) from None
    log.info("隧道盘 cc 目录已就绪 · 版本=%s 写入=%s", meta["version"], ",".join(written) or "-")
    return {"version": meta["version"], "written": written}


def placed_version(root: Path) -> str:
    """``cc\\cloud.json`` 里的版本；没有回空串（只读这个小文件，不联网）。"""
    meta = _read_json(Path(root) / CC_DIR / MANIFEST)
    return str(meta.get("version") or "") if meta else ""


def prepare(root: Path, host: str, *, site: Optional[str] = None, cache: Optional[Path] = None,
            client: Callable[[float], Any] = _client, script: Optional[Path] = None,
            fetch: Optional[Callable[[], Optional[dict]]] = None) -> dict:
    """配对前确保 ``cc\\`` 就绪（缓存 → 下载 → 写入）。失败抛 :class:`PackageError`。"""
    try:
        host = rdp.host(host)
    except rdp.RdpValueError:
        raise PackageError(WRITE_FAILED) from None
    entry = fetch() if fetch is not None else fetch_entry(site, client=client)
    folder, meta = ensure_cached(entry=entry, cache=cache, site=site, client=client)
    return place(root, host, folder, meta, script=script)


def refresh(root: Path, host: str, *, site: Optional[str] = None, client: Callable[[float], Any] = _client,
            fetch: Optional[Callable[[], Optional[dict]]] = None, **kwargs) -> Optional[dict]:
    """点「连接」后：``cc\\`` 里放过、且清单的版本变了才重新准备；``cc\\`` 是空的什么都不做（不联网），版本没变也不写。"""
    current = placed_version(root)
    if not current:
        return None
    entry = fetch() if fetch is not None else fetch_entry(site, client=client)
    if entry is None or entry["version"] == current:
        return None
    return prepare(root, host, site=site, client=client, fetch=lambda: entry, **kwargs)


def install_command(drive: str, token: str) -> str:
    """云端 PowerShell 里打的那一行（令牌已填入）。只放进回包给设置页显示，不进日志、不写文件。"""
    letter = rdp.drive(drive)
    return ("powershell -NoProfile -ExecutionPolicy Bypass -File "
            f"\\\\tsclient\\{letter}\\{CC_DIR}\\{SCRIPT} -Token {token}")


__all__ = ["CHECKSUM", "CODES", "DOWNLOAD_FAILED", "NO_PACKAGE", "PackageError", "WRITE_FAILED", "download",
           "ensure_cached", "fetch_entry", "install_command", "newest_cached", "parse_entry", "place", "placed_version",
           "prepare", "refresh", "script_path", "tunnel_manifest"]
