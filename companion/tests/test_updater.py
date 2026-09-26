# -*- coding: utf-8 -*-
"""自动更新:版本比较 + check_update(拉 latest.json 比对)守门。"""
import httpx

from src.companion import updater
from src.companion.version import version_tuple


def test_version_tuple_compare():
    assert version_tuple("1.2.0") > version_tuple("1.1.0")
    assert version_tuple("1.10.0") > version_tuple("1.9.0")  # 数值比较·非字典序
    assert not version_tuple("1.1.0") > version_tuple("1.1.0")
    assert version_tuple("garbage") == (0,)


def _patch_get(monkeypatch, *, json_data=None, raise_exc=None):
    class _Resp:
        def json(self):
            return json_data

        def raise_for_status(self):
            return None

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, *a, **k):
            if raise_exc:
                raise raise_exc
            return _Resp()

    monkeypatch.setattr(updater.httpx, "Client", _Client)


def test_check_update_returns_newer(monkeypatch):
    _patch_get(monkeypatch, json_data={"version": "1.2.0", "url": "/static/companion/x.exe?v=1"})
    up = updater.check_update("https://pearnly.com", current_version="1.1.0")
    assert up == {"version": "1.2.0", "url": "/static/companion/x.exe?v=1"}


def test_check_update_none_when_same_or_older(monkeypatch):
    _patch_get(monkeypatch, json_data={"version": "1.1.0", "url": "/x.exe"})
    assert updater.check_update("https://pearnly.com", current_version="1.1.0") is None
    _patch_get(monkeypatch, json_data={"version": "1.0.0", "url": "/x.exe"})
    assert updater.check_update("https://pearnly.com", current_version="1.1.0") is None


def test_check_update_none_without_url(monkeypatch):
    _patch_get(monkeypatch, json_data={"version": "9.9.9"})  # 缺 url → 不更新
    assert updater.check_update("https://pearnly.com", current_version="1.1.0") is None


def test_check_update_swallows_network_error(monkeypatch):
    _patch_get(monkeypatch, raise_exc=httpx.ConnectError("boom"))
    assert updater.check_update("https://pearnly.com", current_version="1.1.0") is None


def test_check_update_no_backend():
    assert updater.check_update("", current_version="1.1.0") is None
