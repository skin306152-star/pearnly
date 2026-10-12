"""按需请求里的文件夹请求（小助手侧）：只在本机加过的根目录里列文件、取文件，挡路径穿越与链接跳出。"""

import gzip
import json
import os
import sys
from types import SimpleNamespace

import pytest

from companion import folder_fetch, folder_plan, folder_store
from companion.request_contract import MAX_BYTES, RequestFailed


def _cfg(url="https://a.pearnly.com", token="exp_CONN1_secret"):
    return SimpleNamespace(backend_url=url, bearer_token=token, profile_id="p1", paused=False, paired=True,
                           endpoint_id="", kind="express")


@pytest.fixture
def root(tmp_path):
    base = tmp_path / "Clients"
    (base / "ACME" / "bank").mkdir(parents=True)
    (base / "ACME" / folder_plan.RESULT_FOLDER / "2026-10").mkdir(parents=True)
    (base / "ACME" / "bank" / "kbank-2026-09.pdf").write_bytes(b"%PDF statement")
    (base / "ACME" / "sales.xlsx").write_bytes(b"xlsx")
    (base / "ACME" / "notes.exe").write_bytes(b"MZ")
    (base / "ACME" / "~$sales.xlsx").write_bytes(b"lock")
    (base / "ACME" / ".hidden.pdf").write_bytes(b"x")
    (base / "ACME" / folder_plan.RESULT_FOLDER / "2026-10" / "INV1_2808.xlsx").write_bytes(b"result")
    for name, at in (("sales.xlsx", 2_000_000_000), ("bank/kbank-2026-09.pdf", 1_900_000_000),
                     (folder_plan.RESULT_FOLDER + "/2026-10/INV1_2808.xlsx", 1_800_000_000)):
        os.utime(base / "ACME" / name, (at, at))
    folder_store.set_roots(folder_store.connection_key(_cfg()), [str(base)])
    return base


def _root_id(base):
    return folder_plan.root_id(str(base))


def _listed(result):
    return json.loads(gzip.decompress(result.content).decode("utf-8"))


def test_lists_fetchable_files_newest_first_with_paths_relative_to_the_root(root):
    result = folder_fetch.list_files(_cfg(), {"root_id": _root_id(root), "folder": "", "search": "", "extensions": []})
    data = _listed(result)
    assert [row["path"] for row in data["files"]] == [
        "ACME\\sales.xlsx", "ACME\\bank\\kbank-2026-09.pdf",
        "ACME\\" + folder_plan.RESULT_FOLDER + "\\2026-10\\INV1_2808.xlsx"]
    sales = data["files"][0]
    assert sales["name"] == "sales.xlsx" and sales["size"] == 4 and not sales["result_folder"]
    assert next(row for row in data["files"] if "INV1" in row["path"])["result_folder"] is True
    assert result.meta == {"format": "json.gz", "count": 3} and data["truncated"] is False


def test_folder_search_and_type_narrow_the_list(root):
    rid = _root_id(root)
    data = _listed(folder_fetch.list_files(_cfg(), {"root_id": rid, "folder": "ACME\\bank"}))
    assert [row["path"] for row in data["files"]] == ["ACME\\bank\\kbank-2026-09.pdf"]
    data = _listed(folder_fetch.list_files(_cfg(), {"root_id": rid, "search": "KBANK"}))
    assert [row["name"] for row in data["files"]] == ["kbank-2026-09.pdf"]
    data = _listed(folder_fetch.list_files(_cfg(), {"root_id": rid, "extensions": [".pdf"]}))
    assert [row["name"] for row in data["files"]] == ["kbank-2026-09.pdf"]
    data = _listed(folder_fetch.list_files(_cfg(), {"root_id": rid, "limit": 1}))
    assert len(data["files"]) == 1 and data["truncated"] is True


def test_folders_deeper_than_the_limit_mark_the_list_truncated(root):
    deep = root / "ACME" / "deep"
    chain = deep.joinpath(*[f"d{n}" for n in range(1, folder_fetch.DEPTH + 1)])
    chain.mkdir(parents=True)
    (chain / "last.pdf").write_bytes(b"x")  # 起点下第 DEPTH 层文件夹里的：列得到
    rid = _root_id(root)
    data = _listed(folder_fetch.list_files(_cfg(), {"root_id": rid, "folder": "ACME\\deep"}))
    assert [row["name"] for row in data["files"]] == ["last.pdf"] and data["truncated"] is False
    (chain / "more").mkdir()
    (chain / "more" / "deeper.pdf").write_bytes(b"x")  # 再深一层：进不去，清单不全
    data = _listed(folder_fetch.list_files(_cfg(), {"root_id": rid, "folder": "ACME\\deep"}))
    assert [row["name"] for row in data["files"]] == ["last.pdf"] and data["truncated"] is True


def test_names_with_leading_spaces_are_listed_and_read_as_they_are(root):
    # Windows 会去掉名字末尾的空格，所以只用开头带空格的子文件夹和文件（两端一致：每段原样保留）
    (root / "ACME" / " sub").mkdir()
    (root / "ACME" / " sub" / " scan.pdf").write_bytes(b"%PDF spaced")
    rid = _root_id(root)
    data = _listed(folder_fetch.list_files(_cfg(), {"root_id": rid, "search": "scan"}))
    assert [row["path"] for row in data["files"]] == ["ACME\\ sub\\ scan.pdf"]
    assert folder_fetch.read_file(_cfg(), {"root_id": rid, "path": "ACME\\ sub\\ scan.pdf"}).content == b"%PDF spaced"
    with pytest.raises(RequestFailed) as caught:
        folder_fetch.read_file(_cfg(), {"root_id": rid, "path": "ACME\\ \\scan.pdf"})
    assert caught.value.code == "path_invalid"  # 全是空白的一段不收（服务端同样）


def test_paths_split_the_same_way_as_the_server():
    import importlib.util
    from pathlib import Path

    server = Path(__file__).resolve().parents[2] / "apps/pearnly_cowork/pearnly_cowork/folder_intake/contracts.py"
    if not server.exists():
        pytest.skip("server sources not in this checkout")
    spec = importlib.util.spec_from_file_location("server_folder_contracts", server)
    contracts = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(contracts)
    for path in ("ACME \\ scan.pdf", " ACME/bank\\kbank.pdf ", "ACME\\ \\a.pdf", "..\\x.pdf", "ACME//b.xlsx"):
        try:
            mine = "\\".join(folder_fetch._parts(path, allow_empty=False))
        except RequestFailed:
            mine = None
        try:
            theirs = contracts.fetch_path(path)
        except contracts.ContractError:
            theirs = None
        assert mine == theirs, path


def test_an_unknown_root_or_a_missing_folder_is_refused(root):
    for params, code in (({"root_id": "0123456789abcdef"}, "root_missing"),
                         ({"root_id": _root_id(root), "folder": "NOPE"}, "folder_missing"),
                         ({"root_id": _root_id(root), "folder": "..\\.."}, "path_invalid")):
        with pytest.raises(RequestFailed) as caught:
            folder_fetch.list_files(_cfg(), params)
        assert caught.value.code == code
    # 别的连接（另一个租户）看不到这台电脑给这条连接加的根目录
    with pytest.raises(RequestFailed) as caught:
        folder_fetch.list_files(_cfg(url="https://b.pearnly.com"), {"root_id": _root_id(root)})
    assert caught.value.code == "root_missing"


def test_reads_one_file_inside_the_root(root):
    result = folder_fetch.read_file(_cfg(), {"root_id": _root_id(root), "path": "ACME/bank/kbank-2026-09.pdf"})
    assert result.content == b"%PDF statement"
    assert result.meta["name"] == "kbank-2026-09.pdf"
    # 结果文件夹里的 Pearnly 结果表也能取（会计可能改过）
    result = folder_fetch.read_file(_cfg(), {"root_id": _root_id(root),
                                             "path": "ACME\\" + folder_plan.RESULT_FOLDER + "\\2026-10\\INV1_2808.xlsx"})
    assert result.content == b"result"


@pytest.mark.parametrize("path,code", [
    ("..\\..\\secret.pdf", "path_invalid"),
    ("ACME\\..\\..\\secret.pdf", "path_invalid"),
    ("C:\\Windows\\win.ini", "path_invalid"),
    ("\\\\server\\share\\a.pdf", "path_invalid"),
    ("/etc/passwd.txt", "path_invalid"),
    ("", "path_invalid"),
    ("ACME\\notes.exe", "type_not_allowed"),
    ("ACME\\missing.pdf", "not_found"),
    ("ACME\\bank", "type_not_allowed"),
])
def test_paths_outside_or_not_allowed_are_refused(root, path, code):
    with pytest.raises(RequestFailed) as caught:
        folder_fetch.read_file(_cfg(), {"root_id": _root_id(root), "path": path})
    assert caught.value.code == code


def test_size_and_empty_limits(root, monkeypatch):
    (root / "ACME" / "empty.pdf").write_bytes(b"")
    with pytest.raises(RequestFailed) as caught:
        folder_fetch.read_file(_cfg(), {"root_id": _root_id(root), "path": "ACME\\empty.pdf"})
    assert caught.value.code == "empty"
    monkeypatch.setattr(folder_fetch, "FILE_MAX_BYTES", 5)
    with pytest.raises(RequestFailed) as caught:
        folder_fetch.read_file(_cfg(), {"root_id": _root_id(root), "path": "ACME\\bank\\kbank-2026-09.pdf"})
    assert caught.value.code == "too_large"
    assert folder_fetch.FILE_MAX_BYTES != MAX_BYTES["folder_fetch"]  # 上面改的是测试里的上限
    assert MAX_BYTES["folder_fetch"] == 30 * 1024 * 1024


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need extra rights on Windows")
def test_links_that_escape_the_root_are_neither_listed_nor_read(root, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "payroll.pdf").write_bytes(b"secret")
    os.symlink(outside, root / "ACME" / "linked")
    os.symlink(outside / "payroll.pdf", root / "ACME" / "payroll.pdf")
    data = _listed(folder_fetch.list_files(_cfg(), {"root_id": _root_id(root)}))
    assert not any("payroll" in row["path"] for row in data["files"])
    for path in ("ACME\\linked\\payroll.pdf", "ACME\\payroll.pdf"):
        with pytest.raises(RequestFailed) as caught:
            folder_fetch.read_file(_cfg(), {"root_id": _root_id(root), "path": path})
        assert caught.value.code == "outside_root"


def test_the_extensions_match_the_server_and_chat_attachments():
    import ast
    from pathlib import Path

    repo = Path(__file__).resolve().parents[2]
    server = repo / "apps/pearnly_cowork/pearnly_cowork/folder_intake/contracts.py"
    if not server.exists():
        pytest.skip("server sources not in this checkout")
    for node in ast.parse(server.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "FETCH_EXTENSIONS":
            assert ast.literal_eval(node.value) == folder_fetch.FETCH_EXTENSIONS
            break
    else:
        pytest.fail("FETCH_EXTENSIONS not found on the server side")
