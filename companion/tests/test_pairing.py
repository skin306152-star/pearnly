# -*- coding: utf-8 -*-
"""配对逻辑单测(mock 云端 health + 账套选择)· 账套来自登记表(SCCOMP)。

新模型:客户从登记表名单选一个账套(chosen)→ 锁目录/法定公司名/grid 行号/账套代号 + 上报所选。
"""

from unittest import mock
from types import SimpleNamespace

from src.companion import pairing
from src.companion.account_probe import AccountSet
from src.companion.config import CompanionConfig
from src.companion.profile_store import ProfileStoreError


def _acc(
    path,
    *,
    code="DATAT",
    name="9.ข้อมูลทดสอบ",
    company="บริษัท มานะชัยบริการ จำกัด",
    row=2
):
    return AccountSet(
        code=code,
        name=name,
        company=company,
        path=str(path),
        tax_id="0735527000289",
        writable=True,
        row=row,
    )


def test_dir_missing_on_unmounted_drive_points_to_drive(tmp_path):
    # 账套数据在本机没有的盘符(Q:)→ 指路文案带盘符,不是冷冰冰的"目录不存在"。
    msg = pairing._dir_missing_message(r"Q:\2568\EXP68\68SINCER", "zh")
    assert "Q:" in msg and "映射" in msg  # 人话 + 盘符


def test_dir_missing_generic_when_not_drive_path(tmp_path):
    missing = tmp_path / "nope"  # C: 盘存在,只是子目录不在 → 通用文案
    msg = pairing._dir_missing_message(str(missing), "zh")
    assert str(missing) in msg  # 通用「目录不存在:<路径>」


def test_rejects_bad_token_format(tmp_path):
    r = pairing.pair(
        token="nope", chosen=_acc(tmp_path), express_root=str(tmp_path), lang="zh"
    )
    assert not r.ok and "配对码" in r.error


def test_new_profile_requires_pairing_code(tmp_path, monkeypatch):
    cfg = CompanionConfig(bearer_token="", paired=False, profile_id="new")
    queue = mock.Mock()
    monkeypatch.setattr(pairing, "QueueClient", queue)

    r = pairing.pair(
        token="",
        chosen=_acc(tmp_path),
        all_sets=[_acc(tmp_path)],
        express_root=str(tmp_path),
        config=cfg,
        profile_store=SimpleNamespace(profiles=()),
        lang="zh",
    )

    assert not r.ok and "需要填写配对码" in r.error
    queue.assert_not_called()


def test_rejects_no_chosen(tmp_path):
    r = pairing.pair(
        token="exp_a_b", chosen=None, express_root=str(tmp_path), lang="zh"
    )
    assert not r.ok and "选一个账套" in r.error


def test_rejects_missing_account_dir(tmp_path):
    acc = _acc(tmp_path / "nope")
    r = pairing.pair(token="exp_a_b", chosen=acc, express_root=str(tmp_path), lang="zh")
    assert not r.ok and "目录不存在" in r.error


def test_network_exception_is_logged_but_not_exposed_to_customer(tmp_path, monkeypatch):
    acc = _acc(tmp_path)

    class BrokenQueue:
        def __init__(self, _cfg):
            pass

        def __enter__(self):
            raise RuntimeError("WinError 121 provider stack trace")

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(pairing, "QueueClient", BrokenQueue)

    result = pairing.pair(
        token="exp_e_s",
        chosen=acc,
        all_sets=[acc],
        express_root=str(tmp_path),
        lang="th",
    )

    assert not result.ok and result.error_key == "pair_err_network"
    assert "WinError" not in result.error and "stack trace" not in result.error
    assert "เชื่อมต่อ Pearnly ไม่ได้" in result.error


def test_rejects_disabled_endpoint_403(tmp_path, monkeypatch):
    import httpx

    acc = _acc(tmp_path)
    qc_obj = mock.MagicMock()
    req = httpx.Request("POST", "https://pearnly.com/x")
    resp = httpx.Response(403, request=req)
    qc_obj.health.side_effect = httpx.HTTPStatusError(
        "forbidden", request=req, response=resp
    )
    cm = mock.MagicMock()
    cm.__enter__.return_value = qc_obj
    monkeypatch.setattr(pairing, "QueueClient", lambda cfg: cm)
    fake_cfg = mock.MagicMock()
    fake_cfg.update.return_value = fake_cfg
    monkeypatch.setattr(pairing.CompanionConfig, "load", staticmethod(lambda: fake_cfg))

    r = pairing.pair(
        token="exp_e_s",
        chosen=acc,
        all_sets=[acc],
        express_root=str(tmp_path),
        lang="th",
    )
    assert (
        not r.ok and "ปิดใช้งาน" in r.error
    )  # 403 = endpoint 被停用,泰语走对应文案(非裸中文兜底)


def test_success_locks_selection_and_reports(tmp_path, monkeypatch):
    acc = _acc(tmp_path)
    qc_obj = mock.MagicMock()
    qc_obj.health.return_value = {
        "ok": True,
        "account_set": "DATAT",
        "connection": {
            "endpoint_id": "endpoint-a",
            "endpoint_name": "Cowork Express",
            "pearnly_account": "owner@example.com",
        },
    }
    cm = mock.MagicMock()
    cm.__enter__.return_value = qc_obj
    monkeypatch.setattr(pairing, "QueueClient", lambda cfg: cm)

    fake_cfg = mock.MagicMock()
    fake_cfg.update.return_value = fake_cfg
    monkeypatch.setattr(pairing.CompanionConfig, "load", staticmethod(lambda: fake_cfg))
    monkeypatch.setattr(pairing.autostart, "enable", lambda: None)

    r = pairing.pair(
        token="exp_e_s", chosen=acc, all_sets=[acc], express_root=str(tmp_path)
    )
    assert r.ok and r.company == acc.company and r.account_sets == 1
    kwargs = fake_cfg.update.call_args_list[0].kwargs
    assert kwargs["method"] == "dbf" and kwargs["paired"] is True
    assert kwargs["account_dir"] == str(tmp_path)
    assert kwargs["account_company"] == acc.company  # 法定公司名(PACK 闸)
    assert kwargs["account_set"] == str(
        tmp_path
    )  # 唯一键 = 账套 PATH(= account_dir)·非 COMPCOD
    assert kwargs["account_set_row"] == 2  # = chosen.row(登记表确定性)
    assert kwargs["authorized_roots"] == [str(tmp_path)]
    assert kwargs["endpoint_id"] == "endpoint-a"
    assert kwargs["endpoint_name"] == "Cowork Express"
    assert kwargs["pearnly_account"] == "owner@example.com"
    sel = qc_obj.health.call_args.kwargs["selected"]
    assert (
        sel["account_set"] == str(tmp_path) and sel["account_set_row"] == 2
    )  # 整组 4 键
    assert sel["account_company"] == acc.company and sel["account_dir"] == str(tmp_path)


def test_edit_profile_blank_code_reuses_its_token(tmp_path, monkeypatch):
    acc = _acc(tmp_path)
    cfg = CompanionConfig(
        bearer_token="exp_existing_secret", paired=True, profile_id="profile-a"
    )
    store = mock.Mock()
    store.profiles = ()
    qc_obj = mock.MagicMock()
    qc_obj.health.return_value = {"ok": True}
    cm = mock.MagicMock()
    cm.__enter__.return_value = qc_obj
    seen = {}

    def queue_client(queue_cfg):
        seen["token"] = queue_cfg.bearer_token
        return cm

    monkeypatch.setattr(pairing, "QueueClient", queue_client)

    r = pairing.pair(
        token="",
        chosen=acc,
        all_sets=[acc],
        express_root=str(tmp_path),
        enable_autostart=False,
        config=cfg,
        profile_store=store,
    )

    assert r.ok and seen["token"] == "exp_existing_secret"
    saved = store.save_profile.call_args.args[0]
    assert saved.profile_id == "profile-a" and saved.bearer_token == seen["token"]


def test_edit_profile_preserves_authorized_roots_when_unspecified(
    tmp_path, monkeypatch
):
    root_a = tmp_path / "year-a"
    root_b = tmp_path / "year-b"
    root_a.mkdir()
    cfg = CompanionConfig(
        bearer_token="exp_existing_secret",
        paired=True,
        profile_id="profile-a",
        authorized_roots=[str(root_a), str(root_b)],
    )
    store = mock.Mock(profiles=())
    qc_obj = mock.MagicMock()
    qc_obj.health.return_value = {"ok": True}
    cm = mock.MagicMock()
    cm.__enter__.return_value = qc_obj
    monkeypatch.setattr(pairing, "QueueClient", lambda _cfg: cm)

    result = pairing.pair(
        token="",
        chosen=_acc(root_a),
        all_sets=[_acc(root_a)],
        express_root=str(root_a),
        enable_autostart=False,
        config=cfg,
        profile_store=store,
    )

    assert result.ok
    saved = store.save_profile.call_args.args[0]
    assert saved.authorized_roots == [str(root_a), str(root_b)]


def test_pairing_reports_accounts_from_each_authorized_root(tmp_path, monkeypatch):
    root_a = tmp_path / "year-a"
    root_b = tmp_path / "year-b"
    account_dir_a = root_a / "MAIN"
    account_dir_b = root_b / "MAIN"
    account_dir_a.mkdir(parents=True)
    account_dir_b.mkdir(parents=True)
    account_a = _acc(account_dir_a, name="2019")
    account_b = _acc(account_dir_b, name="2020")
    qc_obj = mock.MagicMock()
    qc_obj.health.return_value = {"ok": True}
    cm = mock.MagicMock()
    cm.__enter__.return_value = qc_obj
    cfg = CompanionConfig(paired=False, profile_id="profile-a")
    store = mock.Mock(profiles=())
    monkeypatch.setattr(pairing, "QueueClient", lambda _cfg: cm)
    monkeypatch.setattr(
        pairing,
        "probe_account_sets",
        lambda root: [account_b] if str(root) == str(root_b) else [account_a],
    )

    result = pairing.pair(
        token="exp_e_s",
        chosen=account_a,
        all_sets=[account_a],
        express_root=str(root_a),
        authorized_roots=[str(root_a), str(root_b)],
        enable_autostart=False,
        config=cfg,
        profile_store=store,
    )

    assert result.ok and result.account_sets == 2
    reported = qc_obj.health.call_args.kwargs["account_sets"]
    assert [item["path"] for item in reported] == [
        str(account_dir_a),
        str(account_dir_b),
    ]


def test_same_directory_can_pair_another_profile(tmp_path, monkeypatch):
    cfg = CompanionConfig(
        bearer_token="exp_original_secret",
        paired=True,
        profile_id="profile-b",
        account_dir=str(tmp_path / "original"),
    )
    store = mock.Mock()
    store.profiles = (
        SimpleNamespace(profile_id="profile-a", account_dir=str(tmp_path)),
    )
    qc_obj = mock.MagicMock()
    qc_obj.health.return_value = {"ok": True}
    cm = mock.MagicMock()
    cm.__enter__.return_value = qc_obj
    monkeypatch.setattr(pairing, "QueueClient", lambda _cfg: cm)

    r = pairing.pair(
        token="exp_new_secret",
        chosen=_acc(tmp_path),
        all_sets=[_acc(tmp_path)],
        express_root=str(tmp_path),
        config=cfg,
        profile_store=store,
        lang="zh",
    )

    assert r.ok
    assert cfg.bearer_token == "exp_original_secret"
    assert cfg.account_dir == str(tmp_path / "original")
    saved = store.save_profile.call_args.args[0]
    assert saved.profile_id == "profile-b"
    assert saved.bearer_token == "exp_new_secret"
    assert saved.account_dir == str(tmp_path)


def test_profile_save_error_keeps_edit_config_unchanged(tmp_path, monkeypatch):
    acc = _acc(tmp_path)
    cfg = CompanionConfig(
        bearer_token="exp_original_secret",
        paired=True,
        profile_id="profile-a",
        account_dir=str(tmp_path / "original"),
    )
    store = mock.Mock()
    store.profiles = ()
    store.save_profile.side_effect = ProfileStoreError("disk unavailable")
    qc_obj = mock.MagicMock()
    qc_obj.health.return_value = {"ok": True}
    cm = mock.MagicMock()
    cm.__enter__.return_value = qc_obj
    monkeypatch.setattr(pairing, "QueueClient", lambda _cfg: cm)

    r = pairing.pair(
        token="exp_new_secret",
        chosen=acc,
        all_sets=[acc],
        express_root=str(tmp_path),
        enable_autostart=False,
        config=cfg,
        profile_store=store,
        lang="zh",
    )

    assert not r.ok and r.error_key == "pair_err_profile_save"
    assert cfg.bearer_token == "exp_original_secret"
    assert cfg.account_dir == str(tmp_path / "original")


def test_probe_when_all_sets_absent_uses_chosen_code(tmp_path, monkeypatch):
    acc = _acc(tmp_path)
    monkeypatch.setattr(pairing, "probe_account_sets", lambda root: [acc])
    qc_obj = mock.MagicMock()
    qc_obj.health.return_value = {"ok": True}  # 云端没回 account_set
    cm = mock.MagicMock()
    cm.__enter__.return_value = qc_obj
    monkeypatch.setattr(pairing, "QueueClient", lambda cfg: cm)
    fake_cfg = mock.MagicMock()
    fake_cfg.update.return_value = fake_cfg
    monkeypatch.setattr(pairing.CompanionConfig, "load", staticmethod(lambda: fake_cfg))
    monkeypatch.setattr(pairing.autostart, "enable", lambda: None)

    r = pairing.pair(token="exp_e_s", chosen=acc, express_root=str(tmp_path))
    assert r.ok
    assert fake_cfg.update.call_args_list[0].kwargs["account_set"] == str(
        tmp_path
    )  # 唯一键 = PATH
