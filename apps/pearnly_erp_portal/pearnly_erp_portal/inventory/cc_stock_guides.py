"""ERP 版 CC 的引导声明：库存四单——出库、调拨、物料调整、入库（ADR-009：产品自己声明；形状见
``pearnly_core.assistant.guide.declaration``；需求评估 ``docs/work/2026-11-19-cc-guided-stock-entries-评估.md``）。
``GUIDES`` 是纯字面量：闸门脚本不导入本模块，直接按源码读它。

- 一种单一份声明（与网页四个菜单一一对应），四份共用一个产品函数模块 `inventory.stock_guide`：方向按 ``ctx["business"]`` 定；
- 只在 ERP × 直写 × 本地 / 云端 Express 下出现（``ledgers``；MR.ERP 账套、没接 Express 的公司、还没挂公司的对话都不出，
  菜单过滤见 `assistant.guide.registry.fits_ledger`）；代录模式不出（``modes`` 只有 chat）；
- 钱和对象（仓库、物料与数量、单价、调入仓库、用途、按台的台）每次明选，推荐值只排第一不预选；日期、账套这类后台能定的自动定；
- 物料按行选仓：选项的码是「仓库 + 物料」（按台管理的带 ``s:`` 前缀，按台那一题按它判出不出），``warehouse`` 只当默认仓；
  调拨照服务端口径单头一出一进（行上的仓就是调出仓）；
- 复核后点「确定」才建单并提交（`inventory.api.api_create_stock_entry`，与网页提交同一次调用，附件一起挂上）；之前只有草稿号字符串，
  不建 ``ERP Stock Entry``。
"""

WRITES = {"stock_out": "stock.out", "stock_transfer": "stock.transfer", "stock_adjust": "stock.adjust", "stock_in": "stock.in"}


def _allowed(user, business) -> bool:
    from pearnly_erp_portal.access import guard

    return bool(user) and user != "Guest" and guard.has(WRITES[business], user)


def allowed_out(user) -> bool:
    """菜单上出不出「出库」：勾了出库功能的人（`access.guard`，同网页出库页）；哪家公司、哪个仓在每一步由服务自己核。"""
    return _allowed(user, "stock_out")


def allowed_transfer(user) -> bool:
    return _allowed(user, "stock_transfer")


def allowed_adjust(user) -> bool:
    return _allowed(user, "stock_adjust")


def allowed_in(user) -> bool:
    return _allowed(user, "stock_in")


GUIDES = [
    {"id": "stock_out", "label_key": "guide.business.stock_out", "icon": "boxes", "order": 61,
     "modes": ["chat"], "ledgers": ["express_local", "express_cloud"],
     "allowed": "pearnly_erp_portal.inventory.cc_stock_guides.allowed_out",
     "opening_key": "guide.open.stock_out", "source": "form", "writes": True,
     "steps": [
         {"key": "day", "input": "date", "rule": "auto",
          "default": "pearnly_erp_portal.inventory.stock_guide.day_default", "check": "pearnly_erp_portal.inventory.stock_guide.day_check"},
         {"key": "purpose", "input": "choice", "rule": "always", "required": False,
          "options": "pearnly_erp_portal.inventory.stock_guide.purpose_options"},
         {"key": "warehouse", "input": "choice", "rule": "always", "search": True,
          "options": "pearnly_erp_portal.inventory.stock_guide.warehouse_options", "default": "pearnly_erp_portal.inventory.stock_guide.warehouse_default"},
         {"key": "rows", "input": "amounts", "rule": "always", "search": True,
          "options": "pearnly_erp_portal.inventory.stock_guide.held_options"},
         {"key": "units", "input": "questions", "rule": "always", "when": {"key": "rows", "starts": "s:"},
          "options": "pearnly_erp_portal.inventory.stock_guide.units_options", "check": "pearnly_erp_portal.inventory.stock_guide.units_check"},
         {"key": "book", "input": "questions", "rule": "auto", "after": ["rows"],
          "options": "pearnly_erp_portal.inventory.stock_guide.book_options", "default": "pearnly_erp_portal.inventory.stock_guide.book_default",
          "apply": "pearnly_erp_portal.inventory.stock_guide.book_apply"},
         {"key": "note", "input": "text", "rule": "always", "required": False},
         {"key": "file", "input": "file", "rule": "always", "required": False,
          "check": "pearnly_erp_portal.inventory.stock_guide.file_check"},
     ],
     "draft": "pearnly_erp_portal.inventory.stock_guide.draft", "review": "pearnly_erp_portal.inventory.stock_guide.review",
     "confirm": "pearnly_erp_portal.inventory.stock_guide.confirm", "result": "pearnly_erp_portal.inventory.stock_guide.result",
     "note": "pearnly_erp_portal.inventory.stock_guide.note", "expires": ["company", "ledger"]},
    {"id": "stock_transfer", "label_key": "guide.business.stock_transfer", "icon": "boxes", "order": 62,
     "modes": ["chat"], "ledgers": ["express_local", "express_cloud"],
     "allowed": "pearnly_erp_portal.inventory.cc_stock_guides.allowed_transfer",
     "opening_key": "guide.open.stock_transfer", "source": "form", "writes": True,
     "steps": [
         {"key": "day", "input": "date", "rule": "auto",
          "default": "pearnly_erp_portal.inventory.stock_guide.day_default", "check": "pearnly_erp_portal.inventory.stock_guide.day_check"},
         {"key": "warehouse", "input": "choice", "rule": "always", "search": True,
          "options": "pearnly_erp_portal.inventory.stock_guide.warehouse_options", "default": "pearnly_erp_portal.inventory.stock_guide.warehouse_default"},
         {"key": "to_warehouse", "input": "choice", "rule": "always", "search": True, "after": ["warehouse"],
          "options": "pearnly_erp_portal.inventory.stock_guide.to_warehouse_options"},
         {"key": "rows", "input": "amounts", "rule": "always", "search": True, "after": ["warehouse"],
          "options": "pearnly_erp_portal.inventory.stock_guide.held_options"},
         {"key": "units", "input": "questions", "rule": "always", "when": {"key": "rows", "starts": "s:"},
          "options": "pearnly_erp_portal.inventory.stock_guide.units_options", "check": "pearnly_erp_portal.inventory.stock_guide.units_check"},
         {"key": "book", "input": "questions", "rule": "auto", "after": ["rows"],
          "options": "pearnly_erp_portal.inventory.stock_guide.book_options", "default": "pearnly_erp_portal.inventory.stock_guide.book_default",
          "apply": "pearnly_erp_portal.inventory.stock_guide.book_apply"},
         {"key": "note", "input": "text", "rule": "always", "required": False},
         {"key": "file", "input": "file", "rule": "always", "required": False,
          "check": "pearnly_erp_portal.inventory.stock_guide.file_check"},
     ],
     "draft": "pearnly_erp_portal.inventory.stock_guide.draft", "review": "pearnly_erp_portal.inventory.stock_guide.review",
     "confirm": "pearnly_erp_portal.inventory.stock_guide.confirm", "result": "pearnly_erp_portal.inventory.stock_guide.result",
     "note": "pearnly_erp_portal.inventory.stock_guide.note", "expires": ["company", "ledger"]},
    {"id": "stock_adjust", "label_key": "guide.business.stock_adjust", "icon": "boxes", "order": 63,
     "modes": ["chat"], "ledgers": ["express_local", "express_cloud"],
     "allowed": "pearnly_erp_portal.inventory.cc_stock_guides.allowed_adjust",
     "opening_key": "guide.open.stock_adjust", "source": "form", "writes": True,
     "steps": [
         {"key": "day", "input": "date", "rule": "auto",
          "default": "pearnly_erp_portal.inventory.stock_guide.day_default", "check": "pearnly_erp_portal.inventory.stock_guide.day_check"},
         {"key": "warehouse", "input": "choice", "rule": "always", "search": True,
          "options": "pearnly_erp_portal.inventory.stock_guide.warehouse_options", "default": "pearnly_erp_portal.inventory.stock_guide.warehouse_default"},
         {"key": "plus", "input": "amounts", "rule": "always", "search": True, "required": False,
          "options": "pearnly_erp_portal.inventory.stock_guide.inbound_options"},
         {"key": "minus", "input": "amounts", "rule": "always", "search": True, "required": False,
          "options": "pearnly_erp_portal.inventory.stock_guide.held_options"},
         {"key": "prices", "input": "amounts", "rule": "always", "zero": True, "after": ["plus"],
          "when": {"key": "plus", "starts": ""},
          "options": "pearnly_erp_portal.inventory.stock_guide.price_options", "default": "pearnly_erp_portal.inventory.stock_guide.price_default"},
         {"key": "plus_units", "input": "questions", "rule": "always", "when": {"key": "plus", "starts": "s:"},
          "options": "pearnly_erp_portal.inventory.stock_guide.plus_units_options", "check": "pearnly_erp_portal.inventory.stock_guide.plus_units_check"},
         {"key": "minus_units", "input": "questions", "rule": "always", "when": {"key": "minus", "starts": "s:"},
          "options": "pearnly_erp_portal.inventory.stock_guide.minus_units_options", "check": "pearnly_erp_portal.inventory.stock_guide.minus_units_check"},
         {"key": "book", "input": "questions", "rule": "auto", "after": ["plus", "minus"],
          "options": "pearnly_erp_portal.inventory.stock_guide.book_options", "default": "pearnly_erp_portal.inventory.stock_guide.book_default",
          "apply": "pearnly_erp_portal.inventory.stock_guide.book_apply"},
         {"key": "note", "input": "text", "rule": "always", "required": False},
         {"key": "file", "input": "file", "rule": "always", "required": False,
          "check": "pearnly_erp_portal.inventory.stock_guide.file_check"},
     ],
     "draft": "pearnly_erp_portal.inventory.stock_guide.draft", "review": "pearnly_erp_portal.inventory.stock_guide.review",
     "confirm": "pearnly_erp_portal.inventory.stock_guide.confirm", "result": "pearnly_erp_portal.inventory.stock_guide.result",
     "note": "pearnly_erp_portal.inventory.stock_guide.note", "expires": ["company", "ledger"]},
    {"id": "stock_in", "label_key": "guide.business.stock_in", "icon": "boxes", "order": 64,
     "modes": ["chat"], "ledgers": ["express_local", "express_cloud"],
     "allowed": "pearnly_erp_portal.inventory.cc_stock_guides.allowed_in",
     "opening_key": "guide.open.stock_in", "source": "form", "writes": True,
     "steps": [
         {"key": "day", "input": "date", "rule": "auto",
          "default": "pearnly_erp_portal.inventory.stock_guide.day_default", "check": "pearnly_erp_portal.inventory.stock_guide.day_check"},
         {"key": "purpose", "input": "choice", "rule": "always", "required": False,
          "options": "pearnly_erp_portal.inventory.stock_guide.purpose_options"},
         {"key": "warehouse", "input": "choice", "rule": "always", "search": True,
          "options": "pearnly_erp_portal.inventory.stock_guide.warehouse_options", "default": "pearnly_erp_portal.inventory.stock_guide.warehouse_default"},
         {"key": "rows", "input": "amounts", "rule": "always", "search": True,
          "options": "pearnly_erp_portal.inventory.stock_guide.inbound_options"},
         {"key": "prices", "input": "amounts", "rule": "always", "zero": True, "after": ["rows"],
          "options": "pearnly_erp_portal.inventory.stock_guide.price_options", "default": "pearnly_erp_portal.inventory.stock_guide.price_default"},
         {"key": "units", "input": "questions", "rule": "always", "after": ["purpose"], "when": {"key": "rows", "starts": "s:"},
          "options": "pearnly_erp_portal.inventory.stock_guide.units_options", "check": "pearnly_erp_portal.inventory.stock_guide.units_check"},
         {"key": "book", "input": "questions", "rule": "auto", "after": ["rows"],
          "options": "pearnly_erp_portal.inventory.stock_guide.book_options", "default": "pearnly_erp_portal.inventory.stock_guide.book_default",
          "apply": "pearnly_erp_portal.inventory.stock_guide.book_apply"},
         {"key": "note", "input": "text", "rule": "always", "required": False},
         {"key": "file", "input": "file", "rule": "always", "required": False,
          "check": "pearnly_erp_portal.inventory.stock_guide.file_check"},
     ],
     "draft": "pearnly_erp_portal.inventory.stock_guide.draft", "review": "pearnly_erp_portal.inventory.stock_guide.review",
     "confirm": "pearnly_erp_portal.inventory.stock_guide.confirm", "result": "pearnly_erp_portal.inventory.stock_guide.result",
     "note": "pearnly_erp_portal.inventory.stock_guide.note", "expires": ["company", "ledger"]},
]
