"""本地样板 Cowork 录入演示数据：走真实的原件建档 → 分组 → 落库 / 原件绑定 / 重复检查，只把"模型识别"换成固定结果。

只对本地样板站点 cowork-a.localhost 运行（容器 pearnly-sample-cowork-bench-1 内、bench 的 Python）；
不调用 OCR 模型、不计费、不连外部系统。公司与税号为虚构测试值，原件用容器内泰文字体 Loma 画成票据。
原件字节是确定的，同一请求键重跑幂等（原件与记录不会重复生成）。
2026-09-26 在 cowork-a.localhost 首次造数时原件字节还不确定，那个站点上再跑会在第一份原件就因
「请求键已用于不同内容」退出（不写任何东西、不重复造数据）；新站点不受影响。
要再造一批给人从头点一遍，带轮次参数（正整数）：请求键另起一套，票号末段加 100×轮次、票面日期后移轮次天数
（日期 + 卖方 + 金额相同也会被查重标成疑似重复），
同一轮次重跑仍幂等。

用法：
    docker cp scripts/sample-cowork-intake-demo.py pearnly-sample-cowork-bench-1:/tmp/
    docker exec -w /home/frappe/frappe-bench/sites pearnly-sample-cowork-bench-1 \
        /home/frappe/frappe-bench/env/bin/python /tmp/sample-cowork-intake-demo.py [轮次] [mrerp|express 账套键]
带目标时（如 `6 mrerp 6:1`）把「新建录入时选的推送目标」写到这批票上（正常流程由识别落库写入），
复核页「提交」就按它推；不带就和以前一样没有目标。
"""
import copy
import datetime
import re
import sys

import frappe
import pymupdf

SITE = "cowork-a.localhost"
ACTOR = "tenant-admin@sample.local"
FONT = "/usr/share/fonts/opentype/tlwg/Loma.otf"
FONT_BOLD = "/usr/share/fonts/opentype/tlwg/Loma-Bold.otf"
KEY = "demo-intake-20260926-"

BUYER = {"buyer_name": "บริษัท ทดสอบการใช้ จำกัด", "buyer_tax": "0105559000001",
         "buyer_branch": "สำนักงานใหญ่", "buyer_addr": "99/1 ถนนสุขุมวิท แขวงคลองตัน เขตคลองเตย กรุงเทพฯ 10110"}


def money(value):
    return f"{float(value):,.2f}"


class Painter:
    def __init__(self, page):
        self.page = page
        page.insert_font(fontname="loma", fontfile=FONT)
        page.insert_font(fontname="lomab", fontfile=FONT_BOLD)

    def text(self, x, y, value, size=9, bold=False, right=False):
        font = "lomab" if bold else "loma"
        if right:
            width = pymupdf.get_text_length(value, fontname="helv", fontsize=size) * 1.1
            x -= width
        self.page.insert_text((x, y), value, fontname=font, fontsize=size)

    def line(self, y, x0=40, x1=555):
        self.page.draw_line((x0, y), (x1, y), color=(0.6, 0.6, 0.6), width=0.6)


def draw_invoice(page, inv, *, top=40, items=None, totals=True, footer=None, title=None):
    p = Painter(page)
    y = top
    p.text(40, y, inv["seller_name"], 13, bold=True)
    p.text(555, y, title or inv.get("title", "ใบกำกับภาษี / ใบแจ้งหนี้"), 12, bold=True, right=True)
    y += 16
    p.text(40, y, inv.get("seller_addr", ""), 8)
    y += 12
    p.text(40, y, f"เลขประจำตัวผู้เสียภาษี {inv['seller_tax']}   {inv.get('seller_branch', '')}", 8)
    p.text(555, y, f"เลขที่ {inv['invoice_number']}", 9, right=True)
    y += 12
    p.text(555, y, f"วันที่ {inv['date_raw']}", 9, right=True)
    y += 16
    p.line(y)
    y += 14
    if inv.get("buyer_name"):
        p.text(40, y, f"ลูกค้า  {inv['buyer_name']}", 9, bold=True)
        y += 12
        p.text(40, y, inv.get("buyer_addr", ""), 8)
        y += 12
        p.text(40, y, f"เลขประจำตัวผู้เสียภาษี {inv.get('buyer_tax', '')}   {inv.get('buyer_branch', '')}", 8)
        y += 16
    p.line(y)
    y += 14
    for label, x, right in (("ลำดับ", 40, False), ("รายการ", 80, False), ("จำนวน", 400, True),
                            ("ราคาต่อหน่วย", 480, True), ("จำนวนเงิน", 555, True)):
        p.text(x, y, label, 9, bold=True, right=right)
    y += 6
    p.line(y)
    rows = inv["items"] if items is None else items
    start = inv["items"].index(rows[0]) + 1 if rows else 1
    for offset, item in enumerate(rows):
        y += 15
        p.text(40, y, str(start + offset), 9)
        p.text(80, y, item["name"], 9)
        p.text(400, y, item["qty"], 9, right=True)
        p.text(480, y, money(item["price"]), 9, right=True)
        p.text(555, y, money(item["subtotal"]), 9, right=True)
    y += 10
    p.line(y)
    if totals:
        for label, key in (("รวมเงิน", "subtotal"), ("ภาษีมูลค่าเพิ่ม 7%", "vat"),
                           ("จำนวนเงินรวมทั้งสิ้น", "total_amount")):
            if inv.get(key):
                y += 15
                p.text(430, y, label, 9, bold=key == "total_amount")
                p.text(555, y, money(inv[key]), 9, bold=key == "total_amount", right=True)
        if inv.get("wht_amount"):
            y += 15
            p.text(430, y, f"หัก ณ ที่จ่าย {inv['wht_rate']}%", 9)
            p.text(555, y, money(inv["wht_amount"]), 9, right=True)
        if inv.get("payment_method"):
            y += 18
            p.text(40, y, f"ชำระโดย: {inv['payment_label']}", 9)
    if footer:
        y += 22
        p.text(555, y, footer, 8, right=True)
    return y


def fields_of(inv, *, items=None, totals=True):
    keys = ("document_type", "invoice_number", "date", "date_raw", "seller_name", "seller_tax", "seller_addr",
            "seller_branch", "buyer_name", "buyer_tax", "buyer_addr", "buyer_branch", "payment_method",
            "wht_rate", "wht_amount", "discount", "notes")
    value = {key: inv[key] for key in keys if inv.get(key)}
    value["items"] = copy.deepcopy(inv["items"] if items is None else items)
    if totals:
        value.update({key: inv[key] for key in ("subtotal", "vat", "total_amount") if inv.get(key)})
    return value


# ── 票面数据（虚构公司，税号为测试号） ─────────────────────────────────────────

SIAM = {**BUYER, "document_type": "tax_invoice", "invoice_number": "IV6909-0012", "date": "2026-09-12",
        "date_raw": "12/09/2569", "seller_name": "บริษัท สยามซัพพลาย จำกัด", "seller_tax": "0105561012345",
        "seller_branch": "สำนักงานใหญ่", "seller_addr": "45 ถนนบางนา-ตราด แขวงบางนา เขตบางนา กรุงเทพฯ 10260",
        "items": [
            {"name": "กระดาษ A4 80 แกรม (รีม)", "qty": "50", "price": "115.00", "subtotal": "5750.00"},
            {"name": "หมึกพิมพ์เลเซอร์ HP 85A", "qty": "4", "price": "950.00", "subtotal": "3800.00"},
            {"name": "แฟ้มเอกสารสันกว้าง", "qty": "30", "price": "45.00", "subtotal": "1350.00"},
            {"name": "ปากกาลูกลื่น (กล่อง 50 ด้าม)", "qty": "4", "price": "180.00", "subtotal": "720.00"},
            {"name": "ลวดเย็บกระดาษ No.10 (กล่อง)", "qty": "20", "price": "14.00", "subtotal": "280.00"},
            {"name": "ค่าขนส่ง", "qty": "1", "price": "600.00", "subtotal": "600.00"}],
        "subtotal": "12500.00", "vat": "875.00", "total_amount": "13375.00",
        "payment_method": "transfer", "payment_label": "โอนเงิน"}

KITCHEN = {"document_type": "receipt", "invoice_number": "RC-0441", "date": "2026-09-18", "date_raw": "18/09/2569",
           "seller_name": "ร้านครัวคุณแม่", "seller_tax": "3100500123456", "seller_branch": "",
           "seller_addr": "12 ซอยสุขุมวิท 22 กรุงเทพฯ", "title": "ใบเสร็จรับเงิน",
           "items": [{"name": "ข้าวกล่องรับรองลูกค้า", "qty": "10", "price": "45.00", "subtotal": "450.00"}],
           "total_amount": "450.00", "payment_method": "cash", "payment_label": "เงินสด"}

FUEL = {"document_type": "simplified_tax_invoice", "invoice_number": "BN2569-88213", "date": "2026-09-18",
        "date_raw": "18/09/2569", "seller_name": "สถานีบริการน้ำมัน บางนาพลัส", "seller_tax": "0105548077711",
        "seller_branch": "สาขาที่ 00012", "seller_addr": "88 ถนนบางนา-ตราด กม.3", "title": "ใบกำกับภาษีอย่างย่อ",
        "items": [{"name": "แก๊สโซฮอล์ 95", "qty": "31.25", "price": "38.40", "subtotal": "1200.00"}],
        "subtotal": "1121.50", "vat": "78.50", "total_amount": "1200.00",
        "payment_method": "card", "payment_label": "บัตรเครดิต"}

CLEAN = {**BUYER, "document_type": "tax_invoice", "invoice_number": "TC-2569/0315", "date": "2026-09-05",
         "date_raw": "05/09/2569", "seller_name": "บริษัท ไทยคลีนนิ่ง เซอร์วิส จำกัด", "seller_tax": "0105560098765",
         "seller_branch": "สำนักงานใหญ่", "seller_addr": "201 ถนนรามคำแหง แขวงหัวหมาก เขตบางกะปิ กรุงเทพฯ 10240",
         "items": [{"name": "ค่าบริการทำความสะอาดสำนักงาน ก.ย. 2569", "qty": "1", "price": "5000.00", "subtotal": "5000.00"}],
         "subtotal": "5000.00", "vat": "350.00", "total_amount": "5350.00", "wht_rate": "3", "wht_amount": "150.00"}

OFFICE = {**BUYER, "document_type": "tax_invoice", "invoice_number": "OM-66-10421", "date": "2026-09-08",
          "date_raw": "08/09/2569", "seller_name": "บริษัท ออฟฟิศเมท ซัพพลาย จำกัด", "seller_tax": "0105555043210",
          "seller_branch": "สาขาที่ 00003", "seller_addr": "7 ถนนพระราม 9 แขวงห้วยขวาง เขตห้วยขวาง กรุงเทพฯ 10310",
          "items": [{"name": "เก้าอี้สำนักงาน", "qty": "2", "price": "1850.00", "subtotal": "3700.00"},
                    {"name": "โต๊ะพับอเนกประสงค์", "qty": "1", "price": "1250.00", "subtotal": "1250.00"}],
          "subtotal": "4950.00", "vat": "346.50", "total_amount": "5296.50"}

AIRCON = {**BUYER, "document_type": "tax_invoice", "invoice_number": "AE-0927", "date": "2026-09-10",
          "date_raw": "10/09/2569", "seller_name": "ห้างหุ้นส่วนจำกัด ช่างเอก แอร์เซอร์วิส", "seller_tax": "0103557012001",
          "seller_branch": "สำนักงานใหญ่", "seller_addr": "56/3 ถนนลาดพร้าว แขวงจอมพล เขตจตุจักร กรุงเทพฯ 10900",
          "items": [{"name": "ล้างเครื่องปรับอากาศ 18000 BTU", "qty": "4", "price": "600.00", "subtotal": "2400.00"},
                    {"name": "เติมน้ำยา R32", "qty": "2", "price": "400.00", "subtotal": "800.00"}],
          "subtotal": "3200.00", "vat": "224.00", "total_amount": "3424.00", "wht_rate": "3", "wht_amount": "96.00"}

SALE = {"document_type": "tax_invoice", "invoice_number": "SV6909-0007", "date": "2026-09-20", "date_raw": "20/09/2569",
        "seller_name": "บริษัท ทดสอบการใช้ จำกัด", "seller_tax": "0105559000001", "seller_branch": "สำนักงานใหญ่",
        "seller_addr": BUYER["buyer_addr"],
        "buyer_name": "บริษัท กรีนฟาร์ม จำกัด", "buyer_tax": "0105562055501", "buyer_branch": "สำนักงานใหญ่",
        "buyer_addr": "300 หมู่ 5 ตำบลบางพลีใหญ่ อำเภอบางพลี สมุทรปราการ 10540",
        "items": [{"name": "บริการติดตั้งระบบบัญชี", "qty": "1", "price": "18000.00", "subtotal": "18000.00"},
                  {"name": "อบรมการใช้งาน (ชั่วโมง)", "qty": "6", "price": "1500.00", "subtotal": "9000.00"}],
        "subtotal": "27000.00", "vat": "1890.00", "total_amount": "28890.00"}

CAFE = {"document_type": "receipt", "invoice_number": "0005821", "date": "2026-09-22", "date_raw": "22/09/2569",
        "seller_name": "ร้านกาแฟบ้านไร่", "seller_tax": "3120100456789", "seller_branch": "",
        "seller_addr": "9 ถนนสีลม กรุงเทพฯ", "title": "ใบเสร็จรับเงิน",
        "items": [{"name": "กาแฟเย็น", "qty": "6", "price": "55.00", "subtotal": "330.00"},
                  {"name": "ขนมปังปิ้ง", "qty": "3", "price": "40.00", "subtotal": "120.00"}],
        "total_amount": "450.00", "payment_method": "cash", "payment_label": "เงินสด"}


def build(files, target=None):
    """files: [(key, filename, direction, payment, [pages])]; page = (draw, fields, extra)."""
    from pearnly_cowork.files import service as originals
    from pearnly_cowork.history.recognition import persist_batch
    from pearnly_cowork.ocr.results import prepare_history_batch
    from pearnly_cowork.ocr.schema import PipelinePageResult, PipelineResult, validate_invoice

    workspace = frappe.get_all("Cowork Workspace", filters={"enabled": 1}, pluck="name")[0]
    report = []
    for key, filename, direction, payment, pages in files:
        with pymupdf.open() as doc:
            for draw, _, _ in pages:
                draw(doc.new_page(width=595, height=842))
            # 固定元数据、不生成新文档 ID：同一份原件每次字节一致，重跑才能按请求键认出是同一份。
            doc.set_metadata({"creationDate": "D:20260926000000", "modDate": "D:20260926000000",
                              "producer": "pearnly sample", "creator": "pearnly sample"})
            data = doc.tobytes(no_new_id=True, garbage=0)
        source = originals.create(workspace, filename, data, KEY + key)
        results = []
        for number, (_, value, extra) in enumerate(pages, 1):
            results.append(PipelinePageResult(page_number=number, invoice=validate_invoice(value, anchor_year=2026),
                layer_chain=["L1", "L2"], final_confidence=0.99, confidence_band="auto", **(extra or {})))
        plan = prepare_history_batch(PipelineResult(pages=results, page_count=len(pages), elapsed_ms=1200),
            expected_page_count=len(pages), workspace=workspace, source="manual",
            direction=direction, payment=payment)
        rows = persist_batch(source["name"], plan["groups"], KEY + key + "-manifest")
        if target:
            for row in rows:
                frappe.db.set_value("Cowork OCR History", row["name"], {"push_connection": target[0],
                    "push_account_set": target[1]}, update_modified=False)
        frappe.db.commit()
        report.append((filename, [(row["invoice_no"], row.get("source_page_indices"), row.get("duplicate_warning"))
                                  for row in rows]))
    return report


def renumber(round_no):
    """第 n 轮：票号最后一段数字加 100×n（保持位数），票面日期后移 n 天。"""
    def bump(match):
        digits = match.group(0)
        return str(int(digits) + 100 * round_no).zfill(len(digits))
    for inv in (SIAM, KITCHEN, FUEL, CLEAN, OFFICE, AIRCON, SALE, CAFE):
        inv["invoice_number"] = re.sub(r"\d+(?!.*\d)", bump, inv["invoice_number"])
        day = datetime.date.fromisoformat(inv["date"]) + datetime.timedelta(days=round_no)
        inv["date"] = day.isoformat()
        inv["date_raw"] = f"{day.day:02d}/{day.month:02d}/{day.year + 543}"


def main():
    global KEY
    round_no = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    adapter, account = (sys.argv[2], sys.argv[3]) if len(sys.argv) > 3 else (None, None)
    if round_no < 0:
        raise SystemExit("round must be a positive integer")
    if round_no:
        KEY += f"r{round_no}-"
        renumber(round_no)
    frappe.init(site=SITE, sites_path="/home/frappe/frappe-bench/sites")
    if frappe.conf.get("pearnly_product") != "cowork" or not SITE.endswith(".localhost"):
        raise SystemExit("local cowork sample site only")
    frappe.connect()
    frappe.set_user(ACTOR)
    try:
        two_page = [
            (lambda page: draw_invoice(page, SIAM, items=SIAM["items"][:4], totals=False, footer="หน้า 1/2 (มีต่อ)"),
             fields_of(SIAM, items=SIAM["items"][:4], totals=False), None),
            (lambda page: draw_invoice(page, SIAM, items=SIAM["items"][4:], footer="หน้า 2/2"),
             fields_of(SIAM, items=SIAM["items"][4:]), None),
        ]
        two_receipts_value = fields_of(KITCHEN)
        two_receipts_value["additional_invoices"] = [fields_of(FUEL)]

        def two_receipts(page):
            draw_invoice(page, KITCHEN, top=40)
            Painter(page).line(420, 20, 575)
            draw_invoice(page, FUEL, top=450)

        missed = {"validation_warnings": ["possible_missed_invoice: page shows 2 invoice-number candidates vs 1 extracted"],
                  "trigger_reasons": ["possible_missed_invoice(L2): 2 invoice-number candidates vs 1 extracted"]}

        def cafe_page(page):
            draw_invoice(page, CAFE)
            next_number = str(int(CAFE["invoice_number"]) + 1).zfill(len(CAFE["invoice_number"]))
            Painter(page).text(40, 520, f"เลขที่ {next_number} (ใบถัดไปถูกพับอยู่ด้านล่าง)", 8)

        files = [
            ("F1", "ใบกำกับภาษี-สยามซัพพลาย-2หน้า.pdf", "purchase", "credit", two_page),
            ("F2", "ใบเสร็จ-2ใบในหน้าเดียว.pdf", "purchase", "cash",
             [(two_receipts, two_receipts_value, None)]),
            ("F3", "บิลค่าใช้จ่าย-3ใบ.pdf", "purchase", "credit", [
                (lambda page: draw_invoice(page, CLEAN), fields_of(CLEAN), None),
                (lambda page: draw_invoice(page, OFFICE), fields_of(OFFICE), None),
                (lambda page: draw_invoice(page, AIRCON), fields_of(AIRCON), None)]),
            ("F4", "ใบกำกับขาย-กรีนฟาร์ม.pdf", "sales", "credit",
             [(lambda page: draw_invoice(page, SALE), fields_of(SALE), None)]),
            ("F5", "ไทยคลีนนิ่ง-ส่งซ้ำ.pdf", "purchase", "credit",
             [(lambda page: draw_invoice(page, CLEAN), fields_of(CLEAN), None)]),
            ("F6", "ใบเสร็จกาแฟ.pdf", "purchase", "cash", [(cafe_page, fields_of(CAFE), missed)]),
        ]
        target = None
        if adapter:
            connection = frappe.get_all("Cowork ERP Connection", filters={"adapter": adapter}, pluck="name")
            if len(connection) != 1:
                raise SystemExit(f"expected exactly one {adapter} connection")
            target = (connection[0], account)
        for filename, rows in build(files, target):
            print(filename)
            for row in rows:
                print("   ", row)
    finally:
        frappe.destroy()


if __name__ == "__main__":
    main()
