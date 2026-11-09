"""「电脑读不到这份账套」的旧原因改记词条（11-09）：CC 推送前请电脑读账套目录、读不到时以前记的是没走词条的英文句子
（"The computer could not read this account set (…). …"），中文界面待处理卡片照样显示英文。改记 `cowork.masters.failed`
（与网页「要选的项」同一句），显示时按看的人的语言翻。只动以这句开头的原因；重跑只会得到同样的值。"""
import frappe

OLD = "The computer could not read this account set ("
NEW = "cowork.masters.failed"


def execute():
    frappe.db.sql("""UPDATE `tabCowork OCR History` SET last_push_reason = %s
        WHERE last_push_reason LIKE %s""", (NEW, OLD + "%"))
