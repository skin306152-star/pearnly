"""收银日汇总：一家公司一天的收银小票合成的一张 Express 现销日汇总（出入库第一期 B3a，`express.pos_day`）。"""

from frappe.model.document import Document

from pearnly_erp_portal.express import ledger_guard


class ERPPOSDaySummary(Document):
    def validate(self):
        ledger_guard.keep_current(self)  # 账本栏只由写账链路写：旧副本保存不盖回（11-28 B ③）
