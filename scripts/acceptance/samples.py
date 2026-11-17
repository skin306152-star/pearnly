#!/usr/bin/env python3
"""验收样票库：按 `tests/acceptance/samples.toml` 的内容现生成票据图片（PNG），给 CC 对话场景放票用（验收测试标准 §3.4）。

    python3 scripts/acceptance/samples.py list
    python3 scripts/acceptance/samples.py render [名字…]      # 不给名字 = 全部；打印生成的路径
    path = samples.resolve("purchase-a1")                    # 驱动放票时取文件（没生成过就现生成）

内容确定（供应商、金额、日期写死在登记里），合计由这里按 Decimal 算并与登记的 `expect` 对一遍——对不上就不生成（样票自己算错了，
拿它验 CC 没有意义）。生成用本机无头 Chrome 截图（`SMOKE_CHROME`，同流程运行器）；没有 Chrome 时抛 Unmet，场景记「没测到」。
文件放 `~/pearnly-devtools/acceptance-samples/`（`PEARNLY_ACC_SAMPLES` 可改），按内容哈希命名，内容没变不重生成。
"""
from __future__ import annotations

import hashlib
import html
import importlib.util
import os
import subprocess
import sys
import tempfile
import tomllib
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
REGISTRY = ROOT / "tests" / "acceptance" / "samples.toml"
CENT = Decimal("0.01")

if "acc_errors" not in sys.modules:
    _spec = importlib.util.spec_from_file_location("acc_errors", HERE / "acc_errors.py")
    sys.modules["acc_errors"] = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(sys.modules["acc_errors"])
Unmet = sys.modules["acc_errors"].Unmet


def home() -> Path:
    path = Path(os.environ.get("PEARNLY_ACC_SAMPLES") or Path.home() / "pearnly-devtools" / "acceptance-samples")
    path.mkdir(parents=True, exist_ok=True)
    return path


def registry(path: Path = REGISTRY) -> dict:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return {"buyer": data["buyer"], "samples": {item["id"]: item for item in data.get("sample", [])}}


def totals(sample: dict) -> dict:
    """行金额、税前合计、税额、含税合计（Decimal，分位四舍五入；税按税前合计一次算）。"""
    rows = [(Decimal(line["qty"]) * Decimal(line["price"])).quantize(CENT, ROUND_HALF_UP) for line in sample["lines"]]
    amount = sum(rows, Decimal("0"))
    vat = (amount * Decimal(sample.get("vat_rate", "7")) / 100).quantize(CENT, ROUND_HALF_UP)
    return {"rows": rows, "amount": amount, "vat": vat, "total": amount + vat}


def check(sample: dict) -> list[str]:
    """样票登记的 `expect` 与按行算出的合计对不对得上。返回对不上的项。"""
    got, want = totals(sample), sample.get("expect") or {}
    return [f"{key}：登记 {want[key]}，按行算 {got[key]}" for key in ("amount", "vat", "total")
            if key in want and Decimal(want[key]) != got[key]]


def thai_date(iso: str) -> str:
    year, month, day = iso.split("-")
    return f"{day}/{month}/{int(year) + 543}"


def money(value: Decimal) -> str:
    return f"{value:,.2f}"


def page(sample: dict, buyer: dict) -> str:
    """一张 A4 的税票（HTML）。只放票面该有的字，不放任何给模型的提示。销售票（``direction = "sales"``）的卖方是验收公司
    （登记的 [buyer]），买方是票上的 ``customer``。"""
    cell = html.escape
    sums = totals(sample)
    supplier, buyer = (buyer, sample["customer"]) if sample.get("direction") == "sales" else (sample["supplier"], buyer)
    lines = "".join(
        f"<tr><td class=c>{index}</td><td>{cell(line['description'])}</td><td class=r>{cell(line['qty'])}</td><td class=c>{cell(line['unit'])}</td>"
        f"<td class=r>{money(Decimal(line['price']))}</td><td class=r>{money(row)}</td></tr>"
        for index, (line, row) in enumerate(zip(sample["lines"], sums["rows"]), 1))
    return f"""<!doctype html><html lang="th"><head><meta charset="utf-8"><style>
body {{ margin: 18mm; font-family: "Sarabun", "Thonburi", "Tahoma", sans-serif; font-size: 12pt; color: #111; }}
h1 {{ font-size: 17pt; text-align: center; margin: 6mm 0; }} table {{ width: 100%; border-collapse: collapse; }}
.items td, .items th {{ border: 1px solid #444; padding: 2mm; }} .r {{ text-align: right; }} .c {{ text-align: center; }}
.box {{ border: 1px solid #444; padding: 3mm; margin: 3mm 0; }} .sum td {{ padding: 1.5mm 2mm; }}
</style></head><body>
<div><b>{cell(supplier['name'])}</b><br>{cell(supplier.get('address', ''))}<br>
เลขประจำตัวผู้เสียภาษี {cell(supplier['tax_id'])} &nbsp; {cell(supplier.get('branch', ''))}</div>
<h1>{cell(sample['title'])}</h1>
<table><tr><td>เลขที่ <b>{cell(sample['number'])}</b></td><td class=r>วันที่ {thai_date(sample['date'])}</td></tr></table>
<div class=box>ลูกค้า: <b>{cell(buyer['name'])}</b> &nbsp; {cell(buyer.get('branch', ''))}<br>{cell(buyer.get('address', ''))}<br>
เลขประจำตัวผู้เสียภาษี {cell(buyer['tax_id'])}</div>
<table class=items><tr><th>ลำดับ</th><th>รายการ</th><th>จำนวน</th><th>หน่วย</th><th>ราคาต่อหน่วย</th><th>จำนวนเงิน</th></tr>{lines}</table>
<table class=sum style="width:60%;margin-left:40%"><tr><td>รวมเป็นเงิน</td><td class=r>{money(sums['amount'])}</td></tr>
<tr><td>ภาษีมูลค่าเพิ่ม {cell(sample.get('vat_rate', '7'))}%</td><td class=r>{money(sums['vat'])}</td></tr>
<tr><td><b>จำนวนเงินรวมทั้งสิ้น</b></td><td class=r><b>{money(sums['total'])}</b></td></tr></table>
</body></html>"""


def chrome() -> str:
    found = os.environ.get("SMOKE_CHROME") or "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    if not Path(found).exists():
        raise Unmet(f"本机没有无头 Chrome（{found}）：生成不了样票")
    return found


def shoot(source: str, target: Path, runner=subprocess.run) -> None:
    """把票面截成一张 A4 比例的图片。出图片不出 PDF：Chrome 打的 PDF 文字层里泰文字形会重复（11-13 实测「บริษัริ ษัท」），
    产品读电子 PDF 的文字层时会被它带偏，那是样票自己的毛病；图片没有文字层，也更像实际收到的拍照 / 扫描件。"""
    with tempfile.TemporaryDirectory() as folder:
        page_file = Path(folder) / "page.html"
        page_file.write_text(source, encoding="utf-8")
        done = runner([chrome(), "--headless", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=2", "--window-size=794,1123",
                       f"--user-data-dir={folder}/profile", f"--screenshot={target}", page_file.as_uri()],
                      capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
    if done.returncode or not target.is_file() or target.stat().st_size < 1000:
        raise Unmet(f"无头 Chrome 没截出样票 {target.name}：{(done.stderr or '').strip()[-200:]}")


def resolve(name: str, printer=shoot, path: Path = REGISTRY) -> Path:
    """样票名 → 本机文件（没生成过、内容变了就现生成）。不认识的名字、合计对不上的样票抛 Unmet。"""
    data = registry(path)
    sample = data["samples"].get(name)
    if sample is None:
        raise Unmet(f"样票库里没有 {name!r}（登记在 tests/acceptance/samples.toml）")
    wrong = check(sample)
    if wrong:
        raise Unmet(f"样票 {name} 登记的合计与按行算的不一致，不生成：" + "；".join(wrong))
    source = page(sample, data["buyer"])
    target = home() / f"{name}-{hashlib.sha256(source.encode('utf-8')).hexdigest()[:10]}.png"
    if not target.is_file():
        printer(source, target)
    return target


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    data = registry()
    if not args or args[0] == "list":
        for name, sample in data["samples"].items():
            sums = totals(sample)
            party = sample["customer"] if sample.get("direction") == "sales" else sample["supplier"]
            print(f"{name}\t{party['code']}\t{sample['number']}\t{sample['date']}\t{money(sums['total'])}\t{'；'.join(check(sample)) or '合计一致'}")
        return 0
    if args[0] == "render":
        for name in args[1:] or list(data["samples"]):
            print(resolve(name))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
