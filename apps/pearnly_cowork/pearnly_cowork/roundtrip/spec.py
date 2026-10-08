# -*- coding: utf-8 -*-
"""ERP 复核工作簿的「回导规格」· 写侧和读侧共用的唯一一份定义。

会计的实际动作是一个闭环:导出 → 在 Excel 里挑错 → 把对的行删掉 → 改错的行 →
去 ERP 删掉错单 → 把改后的表格导回 Pearnly 重推。这份规格保证那个环能闭上。

两条硬约束:

1. **方向不靠猜,靠行所在的 Sheet。** 销项/进项各一张表,判不出方向的落「待判」表。
   会计发现分错了就把那行剪到另一张表 —— 回导时 Sheet 名当显式方向,压过税号自动判定
   (见 services/erp/express_push/direction.explicit_direction)。会计不必学新概念、
   不必填方向字段,挪一行就是改一次分类。

2. **各表守各自的列位合同。** 销项前 12 列是与 MR.ERP 约定的公式合同(不动列位),
   进项沿用 ภ.พ.30 进项税明细的法定列。回导列一律追加在各自合同列之后,不插队。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

# ── Sheet 名 = 方向的唯一事实源 ────────────────────────────────────────────
SHEET_SALES = "ขาย"
SHEET_PURCHASE = "ซื้อ"
SHEET_PENDING = "รอจำแนก"  # 方向判不出 / 被闸拦下 · 会计裁决后挪走
SHEET_SUMMARY = "สรุป"  # 本批新建主档清单 · 只读不回导

DIRECTION_BY_SHEET: Dict[str, Optional[str]] = {
    SHEET_SALES: "sales",
    SHEET_PURCHASE: "purchase",
    SHEET_PENDING: None,
}
DATA_SHEETS: Tuple[str, ...] = (SHEET_SALES, SHEET_PURCHASE, SHEET_PENDING)

# ── 回导列(两张数据表共用 · 追加在各自合同列之后)────────────────────────
# 对手方税号。销项前 12 列的合同里没有这一格,可 ภ.พ.30 销项附表要报买方税号;
# 更要命的是会计把一张进项票剪到销项表时税号会整个丢掉(真跑实测)。故追加在合同列后。
COL_PARTY_TAX = "เลขประจำตัวผู้เสียภาษีคู่ค้า"

# 对方分店号(5 位,เช่น 00018;สำนักงานใหญ่=00000)。连锁(Big C/Lotus's)全部分店共用
# 一个税号,ERP 里却是一分店一档 —— 只有税号会把分店票记到总店头上(2026-08-06 真账实锤)。
# 与进项合同列「สาขา」刻意不同名:同表两列同名会让按列名索引二义。
COL_PARTY_BRANCH = "สาขาคู่ค้า"

# 单据税额(票面印的那个)。销项合同 12 列里的 VAT 是【每行】的,多行票逐行相加与
# 单据税额可能差分币;而票面税额是要进 ภ.พ.30 的法定数字,不能靠我们重算。故原样带走。
COL_DOC_VAT = "ภาษีมูลค่าเพิ่มรวม"

COL_ERP_DOCNUM = "เลขที่เอกสาร ERP"
COL_ERP_ITEM = "รหัสสินค้า ERP"
COL_ERP_PARTY = "รหัสคู่ค้า ERP"
COL_PUSH_STATUS = "สถานะการส่ง"
COL_ROW_KEY = "รหัสอ้างอิงระบบ"

ROUNDTRIP_HEADERS: Tuple[str, ...] = (
    COL_PARTY_TAX,
    COL_PARTY_BRANCH,
    COL_DOC_VAT,
    COL_ERP_DOCNUM,
    COL_ERP_ITEM,
    COL_ERP_PARTY,
    COL_PUSH_STATUS,
    COL_ROW_KEY,
)
ROUNDTRIP_WIDTHS: Tuple[int, ...] = (22, 12, 16, 18, 16, 14, 16, 26)

# 可选列登记表:进了这里的列不参与表头指纹判定。会计手上还有旧版导出的工作簿,
# 可选列混进必需集会让旧文件回导认不出、静默退回通用表格路——加第二个可选列时
# 只登记这一行。
OPTIONAL_HEADERS = frozenset({COL_PARTY_BRANCH})
_REQUIRED_HEADERS: Tuple[str, ...] = tuple(
    h for h in ROUNDTRIP_HEADERS if h not in OPTIONAL_HEADERS
)
REQUIRED_HEADERS = _REQUIRED_HEADERS


# Worksheet formatting belongs to the export adapter; these definitions are pure.

# ── 各表的业务列名(写侧和读侧共认这一份 · 读侧按列名取值不按列位,会计插列也不散架)──
# 销项:前 12 列是 MR.ERP 公式合同(不动列位/公式),列名见 excel_template_th.HEADERS_TH。
# ★ 3/4 列装的是客户名/商品名(写侧填 buyer_name/description,读侧原样读回),表头因此
#   是「ชื่อ…」而非「รหัส…」—— 真 ERP 客户码/商品码在回导列 COL_ERP_PARTY/COL_ERP_ITEM。
#   曾误标成「รหัส…(码)」却填名,会计看着对不上(2026-07-23 交接 F3)。
SALES_COL_DATE = "วันที่"
SALES_COL_INVOICE = "เลขที่"
SALES_COL_PARTY = "ชื่อลูกค้า"
SALES_COL_ITEM = "ชื่อสินค้า"
SALES_COL_QTY = "จำนวน"
SALES_COL_PRICE = "ราคาต่อหน่วย"
SALES_COL_AMOUNT = "จำนวนเงิน"
SALES_COL_PRE_VAT = "รวมจำนวนเงินก่อนVAT"

# 进项:沿用 ภ.พ.30 进项税明细的法定列(每行一张票 · 不拆商品行 —— 费用票整张进一个科目,
# 与销项按行动库存的粒度天然不同,不该硬捏成一样)。
PURCHASE_HEADERS: Tuple[str, ...] = (
    "ลำดับ",  # 序号
    "วันที่",  # 票面日期
    "เลขที่",  # 票号
    "ชื่อผู้ขาย",  # 卖方(供应商)
    "เลขประจำตัวผู้เสียภาษี",  # 卖方税号
    "สาขา",  # 分店
    "มูลค่าก่อนภาษี",  # 税前金额
    "ภาษีซื้อ",  # 进项税
    "รวมทั้งสิ้น",  # 合计
    "หมวดค่าใช้จ่าย",  # 费用类别
)
PURCHASE_WIDTHS: Tuple[int, ...] = (6, 12, 18, 32, 20, 10, 16, 14, 16, 16)

PURCHASE_COL_DATE = PURCHASE_HEADERS[1]
PURCHASE_COL_INVOICE = PURCHASE_HEADERS[2]
PURCHASE_COL_PARTY = PURCHASE_HEADERS[3]
PURCHASE_COL_PARTY_TAX = PURCHASE_HEADERS[4]
PURCHASE_COL_BRANCH = PURCHASE_HEADERS[5]
PURCHASE_COL_PRE_VAT = PURCHASE_HEADERS[6]
PURCHASE_COL_VAT = PURCHASE_HEADERS[7]
PURCHASE_COL_TOTAL = PURCHASE_HEADERS[8]
PURCHASE_COL_CATEGORY = PURCHASE_HEADERS[9]

# 待判表:方向没定,给的是「为什么没定」+ 最低限度的识别信息,会计裁决后剪到上面两张表。
PENDING_COL_REASON = "เหตุผล"
PENDING_HEADERS: Tuple[str, ...] = (
    "วันที่",
    "เลขที่",
    "คู่ค้า",  # 对手方(方向未定时不预设是客户还是供应商)
    "เลขประจำตัวผู้เสียภาษี",
    "รวมทั้งสิ้น",
    PENDING_COL_REASON,
)
PENDING_WIDTHS: Tuple[int, ...] = (12, 18, 32, 20, 16, 36)

PENDING_COL_DATE = PENDING_HEADERS[0]
PENDING_COL_INVOICE = PENDING_HEADERS[1]

# ── 推送状态标签(泰文 · 与表格其余部分同语言)────────────────────────────
_STATUS_LABEL = {
    "success": "สำเร็จ",
    "pending": "รอส่ง",
    "queued": "รอส่ง",
    "failed": "ล้มเหลว",
    "error": "ล้มเหลว",
    "manual": "ตรวจสอบเอง",
    "skipped_dup": "ซ้ำ · ข้าม",
}
STATUS_UNKNOWN = "-"

# ── 按导出人的语言出表(用户 2026-10-08)────────────────────────────────────
# 上面的泰文名是**规格名**:写侧、读侧、回导合同都按它。导出时 Sheet 名、表头与状态按导出人
# 语言换成下表的译名;导回时四种语言(含旧版只有泰文的表)都认回规格名。译名写在这里而不放
# 翻译包:它们是认表的合同,译文日后若改了,已经导出在会计手里的表格仍要导得回来。
# 同一语言里两个规格名不许共用一个译名(读侧按译名反查,见 tests/test_cowork_roundtrip_language.py)。
_TEXTS: Dict[str, Dict[str, str]] = {
    # Sheet
    SHEET_SALES: {"zh": "销项", "en": "Sales", "ja": "売上"},
    SHEET_PURCHASE: {"zh": "进项", "en": "Purchases", "ja": "仕入"},
    SHEET_PENDING: {"zh": "待分类", "en": "To classify", "ja": "未分類"},
    SHEET_SUMMARY: {"zh": "汇总", "en": "Summary", "ja": "集計"},
    # 回导列
    COL_PARTY_TAX: {"zh": "对方税号", "en": "Counterparty tax ID", "ja": "取引先税番号"},
    COL_PARTY_BRANCH: {"zh": "对方分支", "en": "Counterparty branch", "ja": "取引先支店"},
    COL_DOC_VAT: {"zh": "单据税额", "en": "Document VAT", "ja": "伝票税額"},
    COL_ERP_DOCNUM: {"zh": "ERP 单据号", "en": "ERP document no.", "ja": "ERP 伝票番号"},
    COL_ERP_ITEM: {"zh": "ERP 商品编码", "en": "ERP item code", "ja": "ERP 商品コード"},
    COL_ERP_PARTY: {"zh": "ERP 往来单位编码", "en": "ERP party code", "ja": "ERP 取引先コード"},
    COL_PUSH_STATUS: {"zh": "推送状态", "en": "Push status", "ja": "送信状態"},
    COL_ROW_KEY: {"zh": "系统引用码", "en": "System reference", "ja": "システム参照"},
    # 销项
    "วันที่": {"zh": "日期", "en": "Date", "ja": "日付"},
    "เลขที่": {"zh": "单号", "en": "Number", "ja": "番号"},
    "ชื่อลูกค้า": {"zh": "客户名称", "en": "Customer name", "ja": "顧客名"},
    "ชื่อสินค้า": {"zh": "商品名称", "en": "Item name", "ja": "商品名"},
    "จำนวน": {"zh": "数量", "en": "Quantity", "ja": "数量"},
    "ราคาต่อหน่วย": {"zh": "单价", "en": "Unit price", "ja": "単価"},
    "จำนวนเงิน": {"zh": "金额", "en": "Amount", "ja": "金額"},
    "รวมจำนวนเงิน": {"zh": "合计金额", "en": "Total amount", "ja": "合計金額"},
    "รวมจำนวนเงินก่อนVAT": {"zh": "未税合计", "en": "Total before VAT", "ja": "税抜合計"},
    "VAT": {"zh": "增值税", "en": "VAT", "ja": "VAT"},
    "อ้างอิง": {"zh": "参考", "en": "Reference", "ja": "参照"},
    "Status": {"zh": "状态", "en": "Status", "ja": "ステータス"},
    "สถานะสินค้า": {"zh": "商品状态", "en": "Item status", "ja": "商品状態"},
    "สถานะลูกค้า": {"zh": "客户状态", "en": "Customer status", "ja": "顧客状態"},
    "กลุ่มบัญชีสินค้าคงคลัง": {"zh": "存货科目组", "en": "Inventory account group", "ja": "在庫勘定グループ"},
    # 进项
    "ลำดับ": {"zh": "序号", "en": "No.", "ja": "No."},
    "ชื่อผู้ขาย": {"zh": "供应商名称", "en": "Supplier name", "ja": "仕入先名"},
    "เลขประจำตัวผู้เสียภาษี": {"zh": "税号", "en": "Tax ID", "ja": "税番号"},
    "สาขา": {"zh": "分支", "en": "Branch", "ja": "支店"},
    "มูลค่าก่อนภาษี": {"zh": "未税金额", "en": "Amount before VAT", "ja": "税抜金額"},
    "ภาษีซื้อ": {"zh": "进项税额", "en": "Input VAT", "ja": "仕入税額"},
    "รวมทั้งสิ้น": {"zh": "价税合计", "en": "Total", "ja": "合計"},
    "หมวดค่าใช้จ่าย": {"zh": "费用类别", "en": "Expense category", "ja": "費用区分"},
    # 待分类
    "คู่ค้า": {"zh": "往来单位", "en": "Counterparty", "ja": "取引先"},
    PENDING_COL_REASON: {"zh": "原因", "en": "Reason", "ja": "理由"},
    # 汇总
    "ประเภท": {"zh": "类型", "en": "Type", "ja": "種類"},
    "รหัส": {"zh": "编码", "en": "Code", "ja": "コード"},
    "ชื่อ": {"zh": "名称", "en": "Name", "ja": "名称"},
    "เอกสารที่สร้าง": {"zh": "建档单据", "en": "Created in document", "ja": "作成伝票"},
    "สินค้า/บริการ": {"zh": "商品/服务", "en": "Item/Service", "ja": "商品・サービス"},
    "ลูกค้า": {"zh": "客户", "en": "Customer", "ja": "顧客"},
    "ผู้ขาย": {"zh": "供应商", "en": "Supplier", "ja": "仕入先"},
    # 状态
    "สำเร็จ": {"zh": "成功", "en": "Succeeded", "ja": "成功"},
    "รอส่ง": {"zh": "待推送", "en": "Waiting", "ja": "送信待ち"},
    "ล้มเหลว": {"zh": "失败", "en": "Failed", "ja": "失敗"},
    "ตรวจสอบเอง": {"zh": "待人工核对", "en": "Manual check", "ja": "手動確認"},
    "ซ้ำ · ข้าม": {"zh": "重复 · 已跳过", "en": "Duplicate · skipped", "ja": "重複・スキップ"},
    "ใหม่": {"zh": "新建", "en": "New", "ja": "新規"},
    "เดิม": {"zh": "沿用", "en": "Existing", "ja": "既存"},
}
# 读侧只反查 Sheet 名与表头(状态等单元格值不回读)
_READ_NAMES = (SHEET_SALES, SHEET_PURCHASE, SHEET_PENDING, SHEET_SUMMARY, *ROUNDTRIP_HEADERS,
               "วันที่", "เลขที่", "ชื่อลูกค้า", "ชื่อสินค้า", "จำนวน", "ราคาต่อหน่วย", "จำนวนเงิน", "รวมจำนวนเงิน",
               "รวมจำนวนเงินก่อนVAT", "VAT", "อ้างอิง", "Status", "สถานะสินค้า", "สถานะลูกค้า",
               "กลุ่มบัญชีสินค้าคงคลัง", *PURCHASE_HEADERS, *PENDING_HEADERS,
               "ประเภท", "รหัส", "ชื่อ", "เอกสารที่สร้าง")
_CANONICAL: Dict[str, str] = {}
for _name in _READ_NAMES:
    for _text in (_name, *_TEXTS.get(_name, {}).values()):
        _CANONICAL.setdefault(_text.strip().casefold(), _name)
del _name, _text


def localize(name: str, lang: Any = "th") -> str:
    """规格名 → 导出人语言的译名;泰文或不认识的语言原样(泰文就是规格名)。"""
    code = str(lang or "").split("-")[0].lower()
    return _TEXTS.get(name, {}).get(code) or name


def canonical(value: Any) -> str:
    """表格里读到的 Sheet 名 / 表头 → 规格名;不认识的原样返回(会计自己加的列照旧带着)。"""
    text = str(value or "").strip()
    return _CANONICAL.get(text.casefold(), text)


def status_label(status: Any, reason: Any = None, lang: Any = "th") -> str:
    """推送状态 → 表格里给会计看的文本。带原因时附在后面,便于直接判断该不该去 ERP 删单。"""
    s = str(status or "").strip().lower()
    label = _STATUS_LABEL.get(s)
    if not label:
        return STATUS_UNKNOWN
    label = localize(label, lang)
    r = str(reason or "").strip()
    # 原因已按导出人的语言译成整句（`push.export_facts.reason_text`），泰文整句多在 60–160 字；
    # 旧线上截 60 字放的是小助手原文，照截会把译文截断在半句（2026-10-08 改为 200）。
    return f"{label} · {r[:200]}" if r else label


# ── 回导键 ────────────────────────────────────────────────────────────────
# 票号是会计最常改的字段(纠正错号就是这个流程的主要目的之一),按票号配对必断,
# 故另立一列不随内容变的键。格式 PRN:<history_id>:<行序>。
_KEY_PREFIX = "PRN"


def encode_row_key(history_id: Any, line_index: int = 0) -> str:
    hid = str(history_id or "").strip()
    if not hid:
        return ""
    return f"{_KEY_PREFIX}:{hid}:{int(line_index)}"


def decode_row_key(raw: Any) -> Optional[Tuple[str, int]]:
    """解回 (history_id, 行序);不是我们的键(会计手打的备注等)→ None,不硬猜。"""
    s = str(raw or "").strip()
    if not s.startswith(_KEY_PREFIX + ":"):
        return None
    parts = s.split(":")
    if len(parts) != 3 or not parts[1].strip():
        return None
    try:
        return parts[1].strip(), int(parts[2])
    except ValueError:
        return None


# ── 表头指纹:认出这是我们自己导出的工作簿 ────────────────────────────────
def is_roundtrip_sheet(headers: Sequence[Any]) -> bool:
    """表头是否带全回导必需列(可选列不参与判定 · 旧版导出的工作簿也要认得)。
    命中 → 走确定性解析(纯读单元格·不过大模型);不命中 → 交回原有的通用表格路。"""
    got = {str(h).strip() for h in (headers or []) if str(h or "").strip()}
    return all(h in got for h in _REQUIRED_HEADERS)


def sheet_direction(sheet_name: Any) -> Optional[str]:
    """Sheet 名 → 方向。未知表名返回 None(与「待判」同待遇:不自动推)。"""
    return DIRECTION_BY_SHEET.get(str(sheet_name or "").strip())


def is_data_sheet(sheet_name: Any) -> bool:
    return str(sheet_name or "").strip() in DATA_SHEETS


def roundtrip_values(
    *,
    party_tax: Any = "",
    party_branch: Any = "",
    doc_vat: Any = "",
    docnum: Any = "",
    item_code: Any = "",
    party_code: Any = "",
    push_status: Any = None,
    push_reason: Any = None,
    row_key: Any = "",
    lang: Any = "th",
) -> List[Any]:
    """按 ROUNDTRIP_HEADERS 顺序生成这几格的值(写侧唯一入口·保证顺序不漂)。"""
    return [
        str(party_tax or "").strip(),
        str(party_branch or "").strip(),
        doc_vat if isinstance(doc_vat, (int, float)) else (str(doc_vat or "").strip() or None),
        str(docnum or "").strip(),
        str(item_code or "").strip(),
        str(party_code or "").strip(),
        status_label(push_status, push_reason, lang),
        str(row_key or "").strip(),
    ]
