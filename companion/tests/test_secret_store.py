# -*- coding: utf-8 -*-
"""「连 Express 设置」读取口单测(所选账套整组 · 不含登录凭证 · DBF 直写无需密码)。"""
from src.companion.secret_store import ExpressSettings, load_express_settings


class _Cfg:
    """最小 config 替身。"""

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def test_load_express_settings_reads_whole_group():
    cfg = _Cfg(
        method="dbf",
        express_root=r"\\x\70EXP",
        account_dir=r"\\x\70EXP\test",
        account_company="Co",
        account_set="DATAT",
        account_set_row=2,
    )
    st = load_express_settings(cfg)
    assert st.account_dir == r"\\x\70EXP\test" and st.account_company == "Co"
    assert st.account_set == "DATAT" and st.account_set_row == 2 and st.method == "dbf"
    assert st.has_selection() is True


def test_load_express_settings_defaults_method_dbf():
    st = load_express_settings(_Cfg(account_dir="d", account_set="DATAT"))
    assert st.method == "dbf"


def test_express_settings_has_selection_failsafe():
    base = dict(method="dbf", express_root="", express_exe="", account_company="", account_set_row=0)
    assert not ExpressSettings(account_dir="", account_set="", **base).has_selection()
    assert not ExpressSettings(account_dir="d", account_set="", **base).has_selection()
    assert ExpressSettings(account_dir="d", account_set="DATAT", **base).has_selection()
