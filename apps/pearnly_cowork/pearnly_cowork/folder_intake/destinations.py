"""自动录入的推送目标适配：哪种 ERP 能当规则的去向、推送「准备」之后怎么「确认发送」。

全部走现有推送入口（`push.application.service`），这里只登记「确认发送」这一步各家不同的那一小段：
- Express：确认入口要这条连接的小助手凭据（与网页的 `companion.submission.confirm` 同一个判据）；
- 以后要加 MR.ERP 等：在 `ADAPTERS` 登记一个新类，`supported()` 就自动放行，规则、受理、表格都不用改。
  （MR.ERP 的确认在 `application.submit` 里已经顺带排上服务端执行，适配类的 `confirm` 什么都不用做。）

注意：读文件夹的永远是这条连接的 Windows 小助手（配对码身份），所以目前只登记 Express。
"""
import frappe

from pearnly_cowork.companion import credentials
from pearnly_cowork.push.application import service as application


class ExpressTarget:
    adapter = "express"
    intake_target = "express"

    @staticmethod
    def online(connection):
        """这条连接的小助手现在连着（与网页选去向时的「在线」同一判据）。"""
        return credentials.online(connection)

    @staticmethod
    def ready(prepared):
        """准备时小助手报上了这份账套，确认才可能成功。"""
        return bool(prepared.get("has_reported_credential"))

    @staticmethod
    def confirm(connection, prepared, request_key):
        return application.confirm(prepared["name"], prepared["revision"], request_key,
                                   credential=credentials.for_confirmation(connection))


ADAPTERS = {ExpressTarget.adapter: ExpressTarget}


def online(adapter, connection):
    """小助手没连上（重启、断线、电脑关着）是暂时的：自动录入过一会儿再推，不记失败。读不出就当连着，照常走现有提交。"""
    target = ADAPTERS[adapter]
    try:
        return target.online(connection) if hasattr(target, "online") else True
    except Exception:
        frappe.clear_messages()
        return True
    finally:
        frappe.db.commit()  # 只读；判在线会锁住凭据行，读完立刻放开，不带着锁去准备推送（小助手报到也要这把锁）


def ready(adapter, prepared):
    target = ADAPTERS[adapter]
    return target.ready(prepared) if hasattr(target, "ready") else True


def supported(adapter):
    return adapter in ADAPTERS


def of(adapter):
    return ADAPTERS[adapter]
