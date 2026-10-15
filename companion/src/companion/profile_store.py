"""Versioned multi-Profile configuration for the Windows Companion.

The JSON file contains only DPAPI ciphertext. Plain bearer tokens exist only in
the runtime ``CompanionConfig`` objects handed to the scheduler.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import uuid
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Optional

from .config import CONFIG_FILE, CompanionConfig
from .token_protector import (
    ProfileStoreError,
    TokenProtector,
    UnavailableProtector,
    WindowsCurrentUserProtector,
    default_protector,
)

SCHEMA_VERSION = 4
SUPPORTED_SCHEMA_VERSIONS = frozenset({2, 3, SCHEMA_VERSION})
ACCOUNT_MAP_KEYS = (
    "revenue_acc",
    "ar_acc",
    "vat_output_acc",
    "fallback_acc",
    "ap_acc",
    "vat_input_acc",
)


@dataclass(frozen=True)
class GlobalSettings:
    backend_url: str
    poll_interval_ms: int
    debug_mode: bool
    dpi_simulation: Optional[float]
    log_level: str
    max_retry: int
    companion_id: str
    heartbeat_interval_s: int
    autostart: bool
    ui_lang: str
    always_on: bool = False

    @classmethod
    def from_config(cls, cfg: CompanionConfig) -> "GlobalSettings":
        return cls(**{field.name: getattr(cfg, field.name) for field in fields(cls)})

    @classmethod
    def from_dict(cls, raw: dict) -> "GlobalSettings":
        defaults = cls.from_config(CompanionConfig())
        values = asdict(defaults)
        for field in fields(cls):
            if field.name in raw:
                values[field.name] = raw[field.name]
        return cls(**values)


@dataclass(frozen=True)
class ProfileRecord:
    profile_id: str
    token_ciphertext: str
    account_dir: str
    account_set: str
    account_company: str
    account_set_row: int = 1
    express_root: str = ""
    authorized_roots: tuple[str, ...] = ()
    express_exe: str = ""
    method: str = "dbf"
    default_depcod: str = ""
    revenue_acc: str = ""
    ar_acc: str = ""
    vat_output_acc: str = ""
    fallback_acc: str = ""
    ap_acc: str = ""
    vat_input_acc: str = ""
    express_workdate: str = ""
    pack_backup_enabled: bool = True
    pack_backup_dir: str = ""
    pack_backup_keep: int = 3
    paused: bool = False
    endpoint_id: str = ""
    endpoint_name: str = ""
    pearnly_account: str = ""
    kind: str = "express"  # folder = MR.ERP 的小助手成员：只读文件夹，没有 Express 账套
    # 认电脑时服务端说这把密钥不能再用（superseded / claimed_elsewhere，device_link）：这个成员停用，等对话页重新登记
    # 换上新密钥时清空（配对写新密钥时一并清掉）。
    device_state: str = ""

    @classmethod
    def from_dict(cls, raw: dict) -> "ProfileRecord":
        allowed = {field.name for field in fields(cls)}
        clean = {key: value for key, value in raw.items() if key in allowed}
        roots = clean.get("authorized_roots")
        if isinstance(roots, list):
            clean["authorized_roots"] = tuple(
                str(root).strip() for root in roots if str(root).strip()
            )
        elif roots is not None and not isinstance(roots, tuple):
            clean["authorized_roots"] = ()
        try:
            record = cls(**clean)
        except TypeError as exc:
            raise ProfileStoreError("profile record is incomplete") from exc
        if not record.profile_id or not record.token_ciphertext:
            raise ProfileStoreError("profile identity or token is missing")
        if record.kind != "folder" and (not record.account_dir or not record.account_set):
            raise ProfileStoreError("profile account selection is incomplete")
        return record


class ProfileStore:
    def __init__(
        self,
        path: Path,
        global_settings: GlobalSettings,
        profiles: list[ProfileRecord],
        protector: TokenProtector,
        device_cipher: str = "",
    ) -> None:
        self.path = Path(path)
        self.global_settings = global_settings
        self._profiles = list(profiles)
        self._protector = protector
        # 这台电脑的设备密钥（DPAPI 密文，device_link）：一经生成就不换；文件里已有的永远优先（见 _write）。
        self._device_cipher = str(device_cipher or "")

    @property
    def profiles(self) -> tuple[ProfileRecord, ...]:
        return tuple(self._profiles)

    @classmethod
    def load(
        cls, path: Path | None = None, protector: Optional[TokenProtector] = None
    ) -> "ProfileStore":
        target = Path(path or CONFIG_FILE)
        cipher = protector or default_protector()
        if not target.exists():
            store = cls(
                target, GlobalSettings.from_config(CompanionConfig()), [], cipher
            )
            store._write(store.global_settings, [])
            return store
        original = target.read_bytes()
        try:
            raw = json.loads(original.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProfileStoreError("companion config is not valid JSON") from exc
        if raw.get("schema_version") in SUPPORTED_SCHEMA_VERSIONS:
            store = cls._from_versioned(target, raw, cipher)
            if raw.get("schema_version") != SCHEMA_VERSION:
                store._write(store.global_settings, list(store.profiles))
            return store
        return cls._migrate_flat(target, raw, original, cipher)

    @classmethod
    def _from_versioned(
        cls, path: Path, raw: dict, protector: TokenProtector
    ) -> "ProfileStore":
        if not isinstance(raw.get("global"), dict) or not isinstance(
            raw.get("profiles"), list
        ):
            raise ProfileStoreError("profile config schema is invalid")
        global_settings = GlobalSettings.from_dict(raw["global"])
        profiles = [ProfileRecord.from_dict(item) for item in raw["profiles"]]
        store = cls(path, global_settings, profiles, protector, _device_of(raw))
        store._assert_unique_profile_ids(profiles)
        return store

    @classmethod
    def _migrate_flat(
        cls,
        path: Path,
        raw: dict,
        original: bytes,
        protector: TokenProtector,
    ) -> "ProfileStore":
        allowed = {field.name for field in fields(CompanionConfig)}
        legacy = CompanionConfig(
            **{key: value for key, value in raw.items() if key in allowed}
        )
        global_settings = GlobalSettings.from_config(legacy)
        profiles: list[ProfileRecord] = []
        token = str(legacy.bearer_token or "").strip()
        if legacy.paired:
            if not token:
                raise ProfileStoreError("paired legacy config has no token")
            profiles.append(cls._record_from_runtime(legacy, protector))
        store = cls(path, global_settings, profiles, protector)
        try:
            store._write(global_settings, profiles)
        except Exception:
            # Same-directory os.replace keeps the source path intact on every failed write.
            if not path.exists() or path.read_bytes() != original:
                raise ProfileStoreError(
                    "legacy config migration did not preserve the source file"
                )
            raise
        return store

    @staticmethod
    def _profile_id(value: str = "") -> str:
        clean = str(value or "").strip()
        return clean or f"profile-{uuid.uuid4().hex}"

    @classmethod
    def _record_from_runtime(
        cls,
        cfg: CompanionConfig,
        protector: TokenProtector,
        paused: Optional[bool] = None,
    ) -> ProfileRecord:
        token = str(cfg.bearer_token or "").strip()
        if not token:
            raise ProfileStoreError("profile token is empty")
        return ProfileRecord(
            profile_id=cls._profile_id(getattr(cfg, "profile_id", "")),
            token_ciphertext=protector.protect(token),
            account_dir=str(cfg.account_dir or ""),
            account_set=str(cfg.account_set or ""),
            account_company=str(cfg.account_company or ""),
            account_set_row=int(cfg.account_set_row or 0),
            express_root=str(cfg.express_root or ""),
            authorized_roots=tuple(
                str(root).strip()
                for root in (getattr(cfg, "authorized_roots", None) or ())
                if str(root).strip()
            ),
            express_exe=str(cfg.express_exe or ""),
            method=str(cfg.method or "dbf"),
            default_depcod=str(cfg.default_depcod or ""),
            **{key: str(getattr(cfg, key, "") or "") for key in ACCOUNT_MAP_KEYS},
            express_workdate=str(cfg.express_workdate or ""),
            pack_backup_enabled=bool(cfg.pack_backup_enabled),
            pack_backup_dir=str(cfg.pack_backup_dir or ""),
            pack_backup_keep=int(cfg.pack_backup_keep or 0),
            paused=bool(getattr(cfg, "paused", False) if paused is None else paused),
            endpoint_id=str(getattr(cfg, "endpoint_id", "") or ""),
            endpoint_name=str(getattr(cfg, "endpoint_name", "") or ""),
            pearnly_account=str(getattr(cfg, "pearnly_account", "") or ""),
            kind="folder" if getattr(cfg, "kind", "") == "folder" else "express",
            device_state=str(getattr(cfg, "device_state", "") or ""),
        )

    def _runtime(self, record: ProfileRecord) -> CompanionConfig:
        values = asdict(self.global_settings)
        for field in fields(ProfileRecord):
            if field.name not in {"token_ciphertext"}:
                values[field.name] = getattr(record, field.name)
        values["bearer_token"] = self._protector.unprotect(record.token_ciphertext)
        values["paired"] = True
        cfg = CompanionConfig(**values)
        object.__setattr__(cfg, "_source_path", self.path)
        object.__setattr__(cfg, "_profile_save", self.save_profile)
        return cfg

    def runtime_configs(self, *, include_paused: bool = False) -> list[CompanionConfig]:
        return [
            self._runtime(profile)
            for profile in self._profiles
            if include_paused or not profile.paused
        ]

    def active_runtime_configs(self) -> list[CompanionConfig]:
        return self.runtime_configs(include_paused=False)

    def runtime_config(self, profile_id: str) -> CompanionConfig:
        for profile in self._profiles:
            if profile.profile_id == profile_id:
                return self._runtime(profile)
        raise ProfileStoreError("profile not found")

    def new_runtime_config(self) -> CompanionConfig:
        cfg = CompanionConfig(**asdict(self.global_settings))
        cfg.bearer_token = ""
        cfg.profile_id = self._profile_id()
        cfg.paired = False
        object.__setattr__(cfg, "_source_path", self.path)
        object.__setattr__(cfg, "_profile_save", self.save_profile)
        return cfg

    def save_profile(self, cfg: CompanionConfig, paused: Optional[bool] = None) -> Path:
        global_settings = GlobalSettings.from_config(cfg)
        if not cfg.paired and not str(cfg.account_dir or "").strip():
            self._write(global_settings, self._profiles)
            self.global_settings = global_settings
            object.__setattr__(cfg, "_source_path", self.path)
            object.__setattr__(cfg, "_profile_save", self.save_profile)
            return self.path
        record = self._record_from_runtime(cfg, self._protector, paused)
        profiles = list(self._profiles)
        for index, item in enumerate(profiles):
            if item.profile_id == record.profile_id:
                profiles[index] = record
                break
        else:
            profiles.append(record)
        self._assert_unique_profile_ids(profiles)
        self._write(global_settings, profiles)
        self.global_settings = global_settings
        self._profiles = profiles
        cfg.profile_id = record.profile_id
        cfg.paused = record.paused
        object.__setattr__(cfg, "_source_path", self.path)
        object.__setattr__(cfg, "_profile_save", self.save_profile)
        return self.path

    def set_paused(self, profile_id: str, paused: bool) -> None:
        found = False
        profiles = []
        for item in self._profiles:
            if item.profile_id == profile_id:
                item = ProfileRecord(**{**asdict(item), "paused": bool(paused)})
                found = True
            profiles.append(item)
        if not found:
            raise ProfileStoreError("profile not found")
        self._write(self.global_settings, profiles)
        self._profiles = profiles

    def set_device_state(self, profile_id: str, state: str) -> None:
        found = False
        profiles = []
        for item in self._profiles:
            if item.profile_id == profile_id:
                item = ProfileRecord(**{**asdict(item), "device_state": str(state or "")})
                found = True
            profiles.append(item)
        if not found:
            raise ProfileStoreError("profile not found")
        self._write(self.global_settings, profiles)
        self._profiles = profiles

    def device_key(self) -> str:
        """这台电脑（当前 Windows 用户）的设备密钥：文件里有就解开，没有就生成一把存下（device_link）。

        同一进程里几条线程同时第一次取，锁里先重读文件，只生成一次；别的进程（不会有：单实例）也以文件里先写下的为准。"""
        from .device_link import new_device_key

        with _DEVICE_LOCK:
            cipher = self._device_cipher or _read_device_cipher(self.path)
            if cipher:
                self._device_cipher = cipher
                return self._protector.unprotect(cipher)
            value = new_device_key()
            self._device_cipher = self._protector.protect(value)
            self._write(self.global_settings, list(self._profiles))
            return self._protector.unprotect(self._device_cipher)

    def remove_profile(self, profile_id: str) -> None:
        profiles = [item for item in self._profiles if item.profile_id != profile_id]
        if len(profiles) == len(self._profiles):
            raise ProfileStoreError("profile not found")
        self._write(self.global_settings, profiles)
        self._profiles = profiles

    @staticmethod
    def _assert_unique_profile_ids(profiles: list[ProfileRecord]) -> None:
        ids = [profile.profile_id for profile in profiles]
        if len(set(ids)) != len(ids):
            raise ProfileStoreError("duplicate profile identity")

    @staticmethod
    def _document(
        global_settings: GlobalSettings, profiles: list[ProfileRecord], device_cipher: str = ""
    ) -> dict:
        document = {
            "schema_version": SCHEMA_VERSION,
            "global": asdict(global_settings),
            "profiles": [asdict(profile) for profile in profiles],
        }
        if device_cipher:
            # 旧版（≤1.6）读这份文件时忽略这一键（它重写文件会丢掉它，再升级时这台电脑按新电脑登记一次）
            document["device"] = {"key": device_cipher}
        return document

    def _write(
        self, global_settings: GlobalSettings, profiles: list[ProfileRecord]
    ) -> None:
        with _DEVICE_LOCK:   # 本进程里的写一个接一个：读文件里的设备密钥与写回之间不插进别的写
            self._write_locked(global_settings, profiles)

    def _write_locked(
        self, global_settings: GlobalSettings, profiles: list[ProfileRecord]
    ) -> None:
        # 设备密钥一经写下就不换：手里这份配置读得早（还没有设备密钥）时，沿用文件里已有的那把，不把它盖掉。
        on_disk = _read_device_cipher(self.path)
        if on_disk:
            self._device_cipher = on_disk
        document = self._document(global_settings, profiles, self._device_cipher)
        encoded = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode(
            "utf-8"
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=str(self.path.parent)
        )
        temp_path = Path(temp_name)
        try:
            try:
                os.chmod(temp_path, 0o600)
            except OSError:
                pass
            with os.fdopen(fd, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            check = json.loads(temp_path.read_text(encoding="utf-8"))
            parsed = [
                ProfileRecord.from_dict(item) for item in check.get("profiles", [])
            ]
            self._assert_unique_profile_ids(parsed)
            for profile in parsed:
                if not self._protector.unprotect(profile.token_ciphertext):
                    raise ProfileStoreError("profile token verification failed")
            if self._device_cipher and _device_of(check) != self._device_cipher:
                raise ProfileStoreError("device key verification failed")
            os.replace(temp_path, self.path)
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
        except Exception:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise


_DEVICE_LOCK = threading.RLock()


def _device_of(raw) -> str:
    device = raw.get("device") if isinstance(raw, dict) else None
    value = device.get("key") if isinstance(device, dict) else ""
    return value if isinstance(value, str) else ""


def _read_device_cipher(path: Path) -> str:
    try:
        return _device_of(json.loads(Path(path).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return ""


__all__ = [
    "GlobalSettings",
    "ProfileRecord",
    "ProfileStore",
    "ProfileStoreError",
    "TokenProtector",
    "UnavailableProtector",
    "WindowsCurrentUserProtector",
]
