# -*- coding: utf-8 -*-
"""账套探测单测:读 Express 公司登记表 SCCOMP.DBF(合成)· 只读。

钉死:与登录 grid 一致(按 COMPNAM 升序定行号)· 相对路径相对 express_root · 绝对路径跨盘原样 ·
读 ISINFO 补公司名/税号 · 删除记录跳过 · 登记表缺 → RegistryUnavailable(不回退扫文件夹)。
"""

from __future__ import annotations

import sys
from pathlib import Path

import dbf
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from companion.account_probe import (  # noqa: E402
    RegistryUnavailable,
    account_sets_payload,
    chart_accounts_payload,
    probe_account_sets,
    probe_chart_of_accounts,
    probe_default_accounts,
)

_SCCOMP = "COMPNAM C(60); COMPCOD C(10); PATH C(150); GENDAT D; CANDEL C(1)"  # 测试 tmp 路径长·真机 PATH 短
_ISINFO = "THINAM C(40); ENGNAM C(40); TAXID C(15)"


def _sccomp(root: Path):
    sec = root / "secure"
    sec.mkdir(parents=True, exist_ok=True)
    t = dbf.Table(str(sec / "SCCOMP.DBF"), _SCCOMP, codepage="cp874", dbf_type="vfp")
    t.open(dbf.READ_WRITE)
    return t


def _isinfo(account_dir: Path, thinam: str, tax: str):
    account_dir.mkdir(parents=True, exist_ok=True)
    it = dbf.Table(
        str(account_dir / "ISINFO.DBF"), _ISINFO, codepage="cp874", dbf_type="vfp"
    )
    it.open(dbf.READ_WRITE)
    it.append({"THINAM": thinam, "ENGNAM": "co", "TAXID": tax})
    it.close()


def test_probe_matches_grid_sorted_by_compnam(tmp_path):
    root = tmp_path / "70EXP"
    t = _sccomp(root)
    # 故意乱序写入;grid 排序 = COMPNAM 升序("58.."<"9.."<"Z..")
    t.append(
        {"COMPNAM": "Z.ข้อมูลเปล่า", "COMPCOD": "DATAZ", "PATH": "DAT", "CANDEL": "N"}
    )
    t.append(
        {"COMPNAM": "9.ข้อมูลทดสอบ", "COMPCOD": "DATAT", "PATH": "TEST", "CANDEL": "Y"}
    )
    t.append(
        {"COMPNAM": "58ASIA-SPORT", "COMPCOD": "PDATAT", "PATH": "ASIA", "CANDEL": "Y"}
    )
    t.close()
    _isinfo(root / "DAT", "บริษัท เปล่า จำกัด", "0000000000003")
    _isinfo(root / "TEST", "บริษัท มานะชัยบริการ จำกัด", "0735527000289")
    _isinfo(root / "ASIA", "บริษัท เอเซีย สปอร์ต จำกัด", "0000000000001")

    sets = probe_account_sets(root)
    assert [s.code for s in sets] == ["PDATAT", "DATAT", "DATAZ"]  # COMPNAM 升序
    assert [s.row for s in sets] == [1, 2, 3]
    datat = next(s for s in sets if s.code == "DATAT")
    assert datat.row == 2  # == account_set_row(公司 grid 行号)
    assert datat.name == "9.ข้อมูลทดสอบ"  # grid 名(下拉显示)
    assert datat.company == "บริษัท มานะชัยบริการ จำกัด"  # ISINFO 法定名(PACK 闸)
    assert datat.tax_id == "0735527000289"
    assert datat.path == str(root / "TEST")  # 相对 → 相对 express_root


def test_absolute_path_kept_cross_drive(tmp_path):
    root = tmp_path / "70EXP"
    abs_acc = tmp_path / "Sdrive" / "58ASIASP"  # 模拟 S:\... 跨盘
    _isinfo(abs_acc, "บริษัท เอเซีย จำกัด", "0000000000001")
    t = _sccomp(root)
    t.append({"COMPNAM": "P", "COMPCOD": "PDATAT", "PATH": str(abs_acc), "CANDEL": "Y"})
    t.close()
    sets = probe_account_sets(root)
    assert sets[0].path == str(abs_acc)  # 绝对路径原样(不拼 express_root)
    assert sets[0].company == "บริษัท เอเซีย จำกัด"


def test_deleted_records_skipped(tmp_path):
    root = tmp_path / "70EXP"
    t = _sccomp(root)
    t.append({"COMPNAM": "A", "COMPCOD": "AAA", "PATH": "A", "CANDEL": "Y"})
    t.append({"COMPNAM": "B", "COMPCOD": "BBB", "PATH": "B", "CANDEL": "Y"})
    dbf.delete(t[1])  # 删第二条
    t.close()
    assert [s.code for s in probe_account_sets(root)] == ["AAA"]


_GLACC = (
    "ACCNUM C(15); ACCNAM C(50); ACCNAM2 C(50); ACCTYP C(1); STATUS C(1); GROUP C(1)"
)


def _glacc(account_dir: Path):
    account_dir.mkdir(parents=True, exist_ok=True)
    t = dbf.Table(
        str(account_dir / "GLACC.DBF"), _GLACC, codepage="cp874", dbf_type="vfp"
    )
    t.open(dbf.READ_WRITE)
    return t


def test_chart_keeps_only_postable_active(tmp_path):
    acc = tmp_path / "TEST"
    t = _glacc(acc)
    t.append(
        {
            "ACCNUM": "41-01-01-00",
            "ACCNAM": "รายได้จากการขาย",
            "ACCTYP": "0",
            "STATUS": "A",
            "GROUP": "4",
        }
    )
    t.append(
        {
            "ACCNUM": "11-02-01-00",
            "ACCNAM": "ลูกหนี้การค้า",
            "ACCTYP": "0",
            "STATUS": "A",
            "GROUP": "1",
        }
    )
    t.append(
        {
            "ACCNUM": "40-00-00-00",
            "ACCNAM": "รายได้ (สรุป)",
            "ACCTYP": "1",
            "STATUS": "A",
            "GROUP": "4",
        }
    )  # 汇总→排除
    t.append(
        {
            "ACCNUM": "51-99-99-00",
            "ACCNAM": "เลิกใช้",
            "ACCTYP": "0",
            "STATUS": "X",
            "GROUP": "5",
        }
    )  # 停用→排除
    deleted_idx = len(t)
    t.append(
        {
            "ACCNUM": "99-99-99-00",
            "ACCNAM": "ลบทิ้ง",
            "ACCTYP": "0",
            "STATUS": "A",
            "GROUP": "5",
        }
    )
    dbf.delete(t[deleted_idx])  # 删除→排除
    t.close()

    accts = probe_chart_of_accounts(acc)
    assert [a.code for a in accts] == [
        "41-01-01-00",
        "11-02-01-00",
    ]  # 只可贴记+启用+非删
    rev = accts[0]
    assert rev.name == "รายได้จากการขาย" and rev.type == "4"  # 泰文名直出 + 大类


def test_chart_payload_best_effort_empty(tmp_path):
    assert (
        chart_accounts_payload(tmp_path / "nochart") == []
    )  # 无 GLACC → 空(心跳不中断)
    assert chart_accounts_payload("") == []


# ── ISINFO 默认科目向量(G/L Link Account)→ 6 角色 ──
_ISINFO_ACC = (
    "THINAM C(40); TAXID C(15); "
    "ACCNUM02 C(15); ACCNUM04 C(15); ACCNUM06 C(15); "
    "ACCNUM07 C(15); ACCNUM10 C(15); ACCNUM11 C(15)"
)


def _isinfo_acc(account_dir: Path, values: dict):
    account_dir.mkdir(parents=True, exist_ok=True)
    it = dbf.Table(
        str(account_dir / "ISINFO.DBF"), _ISINFO_ACC, codepage="cp874", dbf_type="vfp"
    )
    it.open(dbf.READ_WRITE)
    row = {"THINAM": "co", "TAXID": "0"}
    row.update(values)
    it.append(row)
    it.close()


def test_default_accounts_mapped_by_isinfo_slot(tmp_path):
    # 索引→角色钉死(与 docs 28 §1.2 一致):11=收入 02=应收 10=销项税 04=采购 07=应付 06=进项税。
    acc = tmp_path / "TEST"
    _isinfo_acc(
        acc,
        {
            "ACCNUM11": "41-01-00-00",
            "ACCNUM02": "11-02-01-00",
            "ACCNUM10": "11-05-04-02",
            "ACCNUM04": "11-04-02-00",
            "ACCNUM07": "21-02-01-00",
            "ACCNUM06": "11-05-04-01",
        },
    )
    assert probe_default_accounts(acc) == {
        "revenue_acc": "41-01-00-00",
        "ar_acc": "11-02-01-00",
        "vat_output_acc": "11-05-04-02",
        "fallback_acc": "11-04-02-00",
        "ap_acc": "21-02-01-00",
        "vat_input_acc": "11-05-04-01",
    }


def test_default_accounts_other_code_format(tmp_path):
    # 跨账套:代码格式不同(57ASCRD 风格)同样按槽位读对,不依赖代码段结构。
    acc = tmp_path / "ALT"
    _isinfo_acc(acc, {"ACCNUM11": "4100-10", "ACCNUM06": "1155-02"})
    assert probe_default_accounts(acc) == {
        "revenue_acc": "4100-10",
        "vat_input_acc": "1155-02",
    }


def test_default_accounts_skips_empty_and_missing(tmp_path):
    acc = tmp_path / "TEST"
    _isinfo_acc(acc, {"ACCNUM11": "41-01-00-00", "ACCNUM02": "   "})  # 空槽略过
    assert probe_default_accounts(acc) == {"revenue_acc": "41-01-00-00"}
    assert probe_default_accounts(tmp_path / "none") == {}  # 无 ISINFO
    assert probe_default_accounts("") == {}  # 空目录


def test_heartbeat_account_rows_include_data_root_and_per_account_mapping(tmp_path):
    root = tmp_path / "69EXP"
    account = root / "TEST"
    _isinfo_acc(
        account,
        {
            "ACCNUM11": "41-01-00-00",
            "ACCNUM02": "11-02-01-00",
        },
    )
    table = _sccomp(root)
    table.append(
        {"COMPNAM": "9.TEST", "COMPCOD": "DATAT", "PATH": "TEST", "CANDEL": "Y"}
    )
    table.close()

    rows = account_sets_payload(root)

    assert rows[0]["root"] == str(root)
    assert rows[0]["root_label"] == "69EXP"
    assert rows[0]["mapping"] == {
        "revenue_acc": "41-01-00-00",
        "ar_acc": "11-02-01-00",
    }


def test_lightweight_account_rows_only_read_registry(tmp_path, monkeypatch):
    root = tmp_path / "70EXP"
    table = _sccomp(root)
    table.append(
        {"COMPNAM": "9.TEST", "COMPCOD": "DATAT", "PATH": "TEST", "CANDEL": "Y"}
    )
    table.close()
    monkeypatch.setattr(
        "companion.account_probe._read_isinfo",
        lambda _path: (_ for _ in ()).throw(AssertionError("ISINFO must wait")),
    )
    monkeypatch.setattr(
        "companion.account_probe.probe_default_accounts",
        lambda _path: (_ for _ in ()).throw(AssertionError("mapping must wait")),
    )

    rows = account_sets_payload(root, include_details=False)

    assert rows == [
        {
            "code": "DATAT",
            "name": "9.TEST",
            "path": str(root / "TEST"),
            "row": 1,
            "root": str(root),
            "root_label": "70EXP",
        }
    ]


def test_registry_unavailable_no_folder_fallback(tmp_path):
    root = tmp_path / "70EXP"
    (root / "someacct").mkdir(parents=True)  # 有账套文件夹但没登记表
    _isinfo(root / "someacct", "ตัวอย่างขยะ", "0")  # cp874 只认泰文
    with pytest.raises(RegistryUnavailable):
        probe_account_sets(root)  # 绝不回退扫文件夹捞到 someacct
    assert account_sets_payload(root) == []  # 心跳路径吞错 → []
