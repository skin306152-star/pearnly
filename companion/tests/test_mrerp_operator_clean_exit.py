# -*- coding: utf-8 -*-
"""MR.ERP 代录中途停下的收尾（11-24 主会话）：MR.ERP 存档前服务器上什么都不写，放弃 = 离开表单（回列表），不搬 Express 的取消 / 作废。

1. 没接住的异常也照其它出口收尾：存档前先回列表、存档后记「待查」，再原样交批次（批次整批停、等人）；
2. 守门：``Engine.run`` 的每个出口都收尾——存档前回列表或页面已经没了（被踢、断网、窗口没了），存档后记「待查」；最后一个出口兜住
   一切异常。多出一个不收尾的出口就红。"""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from companion.mrerp_operator import engine as E

ENGINE = Path(E.__file__)


class _Book:
    def __init__(self):
        self.unsure, self.settled = [], []

    def ready(self, _doc_id):
        return True

    def unsure_baseline(self, _doc_id):
        return None

    def mark_unsure(self, doc_id):
        self.unsure.append(doc_id)

    def settle(self, doc_id):
        self.settled.append(doc_id)

    def list_path(self, _draft):
        return "aptran/allview.php"


class _Session:
    def __init__(self):
        self.page, self.kicked, self.visited = object(), False, []

    def goto(self, path):
        self.visited.append(path)
        return {}


def _engine():
    recipe = SimpleNamespace(form=lambda _t: SimpleNamespace(), supported=lambda *_a, **_k: True)
    book, session = _Book(), _Session()
    return E.Engine(session, recipe, book), book, session


@pytest.mark.parametrize("saving", [False, True], ids=["before_save", "after_save"])
def test_an_uncaught_error_is_tidied_like_every_other_exit_then_handed_to_the_batch(saving):
    engine, book, session = _engine()
    draft = SimpleNamespace(doc_id="D1", doc_type="RR")

    def enter(_draft, _form):
        engine.form_open = "D1"  # 新增页已打开、填了一半
        engine._saving = saving
        raise RuntimeError("boom")

    engine._enter = enter
    with pytest.raises(RuntimeError):
        engine.run(draft)
    if saving:
        assert book.unsure == ["D1"] and session.visited == []  # 点了存档：不动页面，记待查，续录前先查
    else:
        assert session.visited == ["aptran/allview.php"] and book.unsure == []  # 存档前：回列表，什么都没写
        assert engine.form_open is None


def test_every_exit_of_run_tidies_up():
    tree = ast.parse(ENGINE.read_text(encoding="utf-8"))
    engine = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Engine")
    run = next(n for n in engine.body if isinstance(n, ast.FunctionDef) and n.name == "run")
    entry = next(t for t in run.body if isinstance(t, ast.Try))
    page_gone = {"Kicked", "NetworkDown", "DeskGone"}  # 会话 / 页面已经没了：关窗时一并丢掉，存档后照样记待查
    after_save_unknown = {"Blocked"}  # 拦下：存档前页面自己的检查，或存档后 allsave 报写库出错（关了自动提交、整单回滚 = 没存）
    for handler in entry.handlers:
        names = {n.id for n in ast.walk(handler.type) if isinstance(n, ast.Name)} if handler.type is not None else {"BaseException"}
        body = {n.attr for n in ast.walk(handler) if isinstance(n, ast.Attribute)}
        assert "mark_unsure" in body or names <= after_save_unknown, f"{names}: an exit after save must leave it to be checked"
        assert "abandon" in body or names <= page_gone, f"{names}: an exit before save must leave the form"
    assert ast.unparse(entry.handlers[-1].type) == "Exception"  # 最后一个出口兜住一切
