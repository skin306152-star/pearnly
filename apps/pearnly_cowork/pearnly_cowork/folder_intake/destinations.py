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


# 替这张单干活的电脑能不能写（`writer_state`）：能写（关着也算）/ 有电脑但够不到（直接按现有原因记下）/ 一台都没有（过一会儿再推）
READY, UNREACHABLE, MISSING = "ready", "unreachable", "missing"


class ExpressTarget:
    adapter = "express"
    intake_target = "express"

    @staticmethod
    def online(connection):
        """池里有能写的电脑此刻在线（与网页选去向时的「在线」同一判据，`companion.pool.state`）。"""
        return pool.for_user(connection)["online"]

    @staticmethod
    def selectable(connection, device=""):
        """当前用户自己的电脑（给了 ``device`` 就是那台，定时任务读文件夹的电脑）里有能写的（关着也算，D4）：能选 Express、
        确认、排队，开机后写（ADR-021 第 51–53 条）。"""
        return pool.for_user(connection, device=device)["writers_known"]

    @staticmethod
    def writer_state(connection, device=""):
        """替这张单干活的电脑（:func:`selectable` 同一口径）：有能写的 → ``READY``；读文件夹的那台（``device``）够不到能写的
        账套（不在这个池里、只读成员、已移除），或负责人有电脑却都够不到 → ``UNREACHABLE``（不是暂时的）；负责人一台电脑都没有
        → ``MISSING``（「未连接电脑」）。"""
        found = pool.for_user(connection, device=device)
        if found.get("writers_known"):
            return READY
        return UNREACHABLE if device or found.get("computers") else MISSING

    @staticmethod
    def ready(prepared):
        """替这张单干活的电脑够得到这份账套，确认才可能成功。"""
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


def writer_state(adapter, connection, *, device=""):
    """定时任务的票现在能不能确认、排队（ADR-021 第 4 批 D4；10-16 第 51、53 条与审查）：

    - ``READY``：Express 读文件夹的那台电脑（``device``；旧任务没记电脑时按负责人自己的电脑）能写，电脑关着也排，开机后写；
    - ``UNREACHABLE``：那台够不到能写的账套（只读成员、账套只在别人电脑上、已移除），或负责人有电脑却都够不到——不是暂时的，
      不再每十分钟重试、6 小时后才记失败：照常准备、确认，由确认按现有原因（``push.no_writer``；跟随公司账套时
      ``cowork.folder.account_set_unreachable``）直接记下；
    - ``MISSING``：负责人一台电脑都没有（「未连接电脑」）：过一会儿再推，最后一次照现有规则记失败。

    MR.ERP 服务端直连，总是 ``READY``。读不出也当 ``READY``，照常走现有提交（确认那一步会按真判据拒）。"""
    target = ADAPTERS[adapter]
    try:
        return target.writer_state(connection, device) if hasattr(target, "writer_state") else READY
    except Exception:
        frappe.clear_messages()
        return READY
    finally:
        frappe.db.commit()  # 只读；不带着读到的锁去准备推送


def intake_state(adapter, connection):
    """录入入口（CC 工具）能不能选这种 ERP、为什么不能（与 LINE `flow._state` 同一口径）：Express 按当前用户自己的电脑——
    有能写的（关着也算，D4）``READY``；有电脑却都够不到能写的账套 ``UNREACHABLE``（「没有能写的电脑」，如账套只在别人电脑的
    C 盘上）；一台电脑都没有 ``MISSING``（「未连接电脑」，ADR-021 第 53 条）。MR.ERP 服务端直连，总能选。读不出当
    ``MISSING``（与 LINE 一样，不让人选一个确认不了的去向）。"""
    target = ADAPTERS[adapter]
    try:
        return target.writer_state(connection) if hasattr(target, "writer_state") else READY
    except (frappe.PermissionError, frappe.ValidationError, frappe.DoesNotExistError):
        frappe.clear_messages()
        return MISSING


def ready(adapter, prepared):
    target = ADAPTERS[adapter]
    return target.ready(prepared) if hasattr(target, "ready") else True


def supported(adapter):
    return adapter in ADAPTERS


def of(adapter):
    return ADAPTERS[adapter]
