"""自动录入的推送目标适配：哪种 ERP 能当规则的去向、推送「准备」之后怎么「确认发送」。

全部走现有推送入口（`push.application.service`），这里只登记「确认发送」这一步各家不同的那一小段：
- Express：确认不要哪台电脑的密钥（ADR-021）：池里至少一台电脑够得到这份账套就能确认、排队（与网页的
  `companion.submission.confirm` 同一个判据）；
- MR.ERP：`application.submit` 里已经顺带排上服务端执行，确认这一步什么都不用做；推送不经小助手，总是「在线」。

读文件夹的永远是这条连接的 Windows 小助手（配对码身份）：MR.ERP 连接也能发配对码，那个小助手成员只读文件夹
（2026-10-09，`companion/credentials.FOLDER_ONLY_ADAPTERS`）。
"""
import frappe

from pearnly_cowork.companion import pool
from pearnly_cowork.push.application import service as application


class ExpressTarget:
    adapter = "express"
    intake_target = "express"

    @staticmethod
    def online(connection):
        """池里有能写的电脑此刻在线（与网页选去向时的「在线」同一判据，`companion.pool.state`）。"""
        return pool.for_user(connection)["online"]

    @staticmethod
    def selectable(connection):
        """池里有能写的电脑（关着也算，D4）：能选 Express、确认、排队，开机后写。"""
        return pool.for_user(connection)["writers_known"]

    @staticmethod
    def ready(prepared):
        """池里有电脑够得到这份账套，确认才可能成功。"""
        return bool(prepared.get("has_writer"))

    @staticmethod
    def confirm(connection, prepared, request_key):
        return application.confirm(prepared["name"], prepared["revision"], request_key)


class MrerpTarget:
    adapter = "mrerp"
    intake_target = "mrerp"

    @staticmethod
    def confirm(connection, prepared, request_key):
        return None


ADAPTERS = {ExpressTarget.adapter: ExpressTarget, MrerpTarget.adapter: MrerpTarget}


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


def selectable(adapter, connection):
    """录入入口（CC 工具）能不能选这种 ERP：Express 要池里有能写的电脑（关着也算，D4）；MR.ERP 服务端直连，总能选。
    读不出当不能选（与 LINE 一样，不让人选一个确认不了的去向）。"""
    target = ADAPTERS[adapter]
    try:
        return target.selectable(connection) if hasattr(target, "selectable") else True
    except (frappe.PermissionError, frappe.ValidationError, frappe.DoesNotExistError):
        frappe.clear_messages()
        return False


def ready(adapter, prepared):
    target = ADAPTERS[adapter]
    return target.ready(prepared) if hasattr(target, "ready") else True


def supported(adapter):
    return adapter in ADAPTERS


def of(adapter):
    return ADAPTERS[adapter]
