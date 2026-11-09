"""合成「同一买方 24 张销售税票」的测试 PDF（场景 `operator_party_once.py` 的素材，11-08 生产事故的同形数据，不是真实客户文件）。

每页一张泰文销售税票：卖方 บริษัท ชินเซียร์ ไอซ์ จำกัด（合成税号）、买方 บมจ.บิ๊กซี ซูเปอร์เซ็นเตอร์ (สำนักงานใหญ่)（合成税号、分店 00000）、
单号 IV69/00751 起连号、日期 2569 年 10 月（佛历，即 2026-10）、一行商品、合计 286.76（268.00 + 7% 18.76）、赊销 30 天。
成品是只有图的 PDF（每页 150 dpi 灰度图，同扫描件）：weasyprint 排出的泰文字形对、但文字层映射是乱的（ำ、ที่ 抽出来是别的字），
文字层会误导识别，所以栅格化后不留文字层。只在回归实验栈的 bench 容器里跑（要 weasyprint、PyMuPDF 与 ERP 打印字体 Prompt）：

    docker exec -i pearnly-parity-lab-bench-1 /home/frappe/frappe-bench/env/bin/python - /tmp/party-once.pdf < scripts/scenarios/make_party_once_pdf.py
    docker cp pearnly-parity-lab-bench-1:/tmp/party-once.pdf tests/fixtures/scenarios/party-once-24-sales.pdf
"""
import sys

PAGES = 24
FIRST = 751
SELLER = ("บริษัท ชินเซียร์ ไอซ์ จำกัด", "0105561077332", "99/9 หมู่ 4 ตำบลบางพลีใหญ่ อำเภอบางพลี จังหวัดสมุทรปราการ 10540")
BUYER = ("บมจ.บิ๊กซี ซูเปอร์เซ็นเตอร์ (สำนักงานใหญ่)", "0107536999991", "97/11 ถนนราชดำริห์ แขวงลุมพินี เขตปทุมวัน กรุงเทพมหานคร 10330")
LINE = ("IC-020", "น้ำแข็งหลอด ถุง 20 กก.", "4", "ถุง", "67.00", "268.00")
SUBTOTAL, VAT, TOTAL, WORDS = "268.00", "18.76", "286.76", "สองร้อยแปดสิบหกบาทเจ็ดสิบหกสตางค์"
FONT = "/home/frappe/frappe-bench/apps/pearnly_erp_portal/pearnly_erp_portal/public/fonts/print/Prompt-Regular.ttf"

CSS = f"""
@font-face {{ font-family: th; src: url(file://{FONT}); }}
@page {{ size: A4; margin: 16mm; }}
body {{ font-family: th; font-size: 11pt; color: #000; }}
.page {{ page-break-after: always; }}
.page:last-child {{ page-break-after: auto; }}
h1 {{ font-size: 15pt; margin: 0; }}
h2 {{ font-size: 13pt; text-align: center; margin: 14px 0 8px; }}
table {{ width: 100%; border-collapse: collapse; }}
.box td {{ vertical-align: top; padding: 2px 4px; }}
.lines th, .lines td {{ border: 1px solid #000; padding: 4px 6px; }}
.num {{ text-align: right; }}
.sum td {{ padding: 3px 6px; }}
"""


def page(index):
    number = f"IV69/{FIRST + index:05d}"
    date = f"{index + 1:02d}/10/2569"
    code, name, qty, unit, price, amount = LINE
    return f"""<div class="page">
<h1>{SELLER[0]} (สำนักงานใหญ่)</h1>
<div>{SELLER[2]}</div>
<div>เลขประจำตัวผู้เสียภาษีอากร {SELLER[1]} สาขาที่ 00000</div>
<h2>ใบกำกับภาษี / ใบแจ้งหนี้ (ต้นฉบับ)<br>TAX INVOICE / INVOICE</h2>
<table class="box"><tr>
<td style="width:62%">ลูกค้า: {BUYER[0]}<br>{BUYER[2]}<br>เลขประจำตัวผู้เสียภาษีอากร {BUYER[1]} สาขาที่ 00000</td>
<td>เลขที่: {number}<br>วันที่: {date}<br>เงื่อนไขการชำระเงิน: เครดิต 30 วัน</td>
</tr></table>
<br>
<table class="lines">
<tr><th>ลำดับ</th><th>รหัสสินค้า</th><th>รายการ</th><th>จำนวน</th><th>หน่วย</th><th>ราคาต่อหน่วย</th><th>จำนวนเงิน</th></tr>
<tr><td>1</td><td>{code}</td><td>{name}</td><td class="num">{qty}</td><td>{unit}</td><td class="num">{price}</td><td class="num">{amount}</td></tr>
</table>
<table class="sum" style="width:50%; margin-left:50%">
<tr><td>รวมเงิน</td><td class="num">{SUBTOTAL}</td></tr>
<tr><td>ภาษีมูลค่าเพิ่ม 7%</td><td class="num">{VAT}</td></tr>
<tr><td><b>จำนวนเงินรวมทั้งสิ้น</b></td><td class="num"><b>{TOTAL}</b></td></tr>
</table>
<div>({WORDS})</div>
<br><br>
<table class="box"><tr><td>ผู้รับสินค้า ....................</td><td>ผู้มีอำนาจลงนาม ....................</td></tr></table>
</div>"""


def main(out):
    import pymupdf
    from weasyprint import HTML

    html = f"<html><head><meta charset='utf-8'><style>{CSS}</style></head><body>{''.join(page(i) for i in range(PAGES))}</body></html>"
    typeset = pymupdf.open(stream=HTML(string=html).write_pdf(), filetype="pdf")
    scanned = pymupdf.open()
    for sheet in typeset:
        picture = sheet.get_pixmap(dpi=150, colorspace=pymupdf.csGRAY)
        blank = scanned.new_page(width=sheet.rect.width, height=sheet.rect.height)
        blank.insert_image(blank.rect, stream=picture.tobytes("jpeg", jpg_quality=80))
    scanned.set_metadata({})
    scanned.save(out, garbage=4, deflate=True)
    print(out)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/party-once.pdf")
