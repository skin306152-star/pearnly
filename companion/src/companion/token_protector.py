"""Current-user protection for Companion Profile bearer tokens."""

from __future__ import annotations

import base64
import ctypes
import sys
from typing import Any, Protocol

from ctypes import wintypes

TOKEN_PREFIX = "dpapi-current-user:v1:"


class ProfileStoreError(RuntimeError):
    pass


class TokenProtector(Protocol):
    def protect(self, value: str) -> str: ...

    def unprotect(self, value: str) -> str: ...


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


class WindowsCurrentUserProtector:
    """Windows DPAPI with CurrentUser scope and no UI prompts."""

    _UI_FORBIDDEN = 0x1

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise ProfileStoreError("Windows CurrentUser DPAPI is unavailable")

    @staticmethod
    def _protect_api():
        api = ctypes.WinDLL("crypt32", use_last_error=True).CryptProtectData
        api.argtypes = [
            ctypes.POINTER(_DataBlob),
            wintypes.LPCWSTR,
            ctypes.POINTER(_DataBlob),
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(_DataBlob),
        ]
        api.restype = wintypes.BOOL
        return api

    @staticmethod
    def _unprotect_api():
        api = ctypes.WinDLL("crypt32", use_last_error=True).CryptUnprotectData
        api.argtypes = [
            ctypes.POINTER(_DataBlob),
            ctypes.POINTER(wintypes.LPWSTR),
            ctypes.POINTER(_DataBlob),
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(_DataBlob),
        ]
        api.restype = wintypes.BOOL
        return api

    @staticmethod
    def _input_blob(raw: bytes) -> tuple[_DataBlob, Any]:
        buffer = ctypes.create_string_buffer(raw)
        blob = _DataBlob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
        return blob, buffer

    @staticmethod
    def _output_bytes(blob: _DataBlob) -> bytes:
        try:
            return ctypes.string_at(blob.pbData, blob.cbData)
        finally:
            local_free = ctypes.WinDLL("kernel32", use_last_error=True).LocalFree
            local_free.argtypes = [ctypes.c_void_p]
            local_free.restype = ctypes.c_void_p
            local_free(blob.pbData)

    def protect(self, value: str) -> str:
        if not value:
            raise ProfileStoreError("profile token is empty")
        source, keepalive = self._input_blob(value.encode("utf-8"))
        target = _DataBlob()
        ok = self._protect_api()(
            ctypes.byref(source),
            "Pearnly Companion profile token",
            None,
            None,
            None,
            self._UI_FORBIDDEN,
            ctypes.byref(target),
        )
        del keepalive
        if not ok:
            raise ProfileStoreError(f"DPAPI protect failed: {ctypes.get_last_error()}")
        return TOKEN_PREFIX + base64.b64encode(self._output_bytes(target)).decode(
            "ascii"
        )

    def unprotect(self, value: str) -> str:
        if not isinstance(value, str) or not value.startswith(TOKEN_PREFIX):
            raise ProfileStoreError("profile token ciphertext is invalid")
        try:
            encrypted = base64.b64decode(value[len(TOKEN_PREFIX) :], validate=True)
        except (ValueError, TypeError) as exc:
            raise ProfileStoreError("profile token ciphertext is invalid") from exc
        source, keepalive = self._input_blob(encrypted)
        target = _DataBlob()
        ok = self._unprotect_api()(
            ctypes.byref(source),
            None,
            None,
            None,
            None,
            self._UI_FORBIDDEN,
            ctypes.byref(target),
        )
        del keepalive
        if not ok:
            raise ProfileStoreError(
                f"DPAPI unprotect failed: {ctypes.get_last_error()}"
            )
        try:
            return self._output_bytes(target).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProfileStoreError("profile token plaintext is invalid") from exc


class UnavailableProtector:
    """Fail closed outside Windows unless tests inject an explicit protector."""

    def protect(self, value: str) -> str:
        raise ProfileStoreError(
            "Windows CurrentUser DPAPI is required for profile tokens"
        )

    def unprotect(self, value: str) -> str:
        raise ProfileStoreError(
            "Windows CurrentUser DPAPI is required for profile tokens"
        )


def default_protector() -> TokenProtector:
    return (
        WindowsCurrentUserProtector()
        if sys.platform == "win32"
        else UnavailableProtector()
    )


__all__ = [
    "ProfileStoreError",
    "TokenProtector",
    "UnavailableProtector",
    "WindowsCurrentUserProtector",
    "default_protector",
]
