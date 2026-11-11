"""一笔收款（Express RE）：只经 `collection.service` / `collection.push` 写；原生读按 `collection.access`（收款员只看自己的，
能看收款的看本公司全部）。名字就是记录号（写进 Express 抬头 YOUREF），由服务层生成。"""
from frappe.model.document import Document


class ERPCollection(Document):
    pass
