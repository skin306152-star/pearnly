"""侧栏标题（ADR-019，同 Claude：主题名词短语）。

- 首条消息原文先当临时标题（`start`，只写本站表，不拖慢发送），记 `title_source = message`。
- 第一轮结束后在后台概括一次（`summarize`）：推送线程看到这一轮结束时排（`turn_ended`）；推送线程没看到的（进程重启、
  过了时限），下一条消息或任一页面在对话空闲时读运行状态时补排。带上首条消息与 CC 第一段回复的开头（从引擎会话读），
  用引擎目录登记的便宜模型，按显示宽度卡长度（`contracts.make_title`：超宽重问一次，仍超宽在词或字形簇边界收短）。
- 每段对话只概括一次：任务先在行锁下把状态从 message 改成 summarizing 才调模型，同时排了几次也只有一次调到模型。
  概括不出来（模型出错、没给）保留临时标题，同样算概括过，不再重试。
- 用户手动改过名（`title_source = user`，见 `sessions.rename`）就不再自动覆盖：概括期间改了名的，写回前在行锁下核对，
  以用户的名字为准。
- 标题变了（临时标题、概括出的标题）经 `sidebar` 推给会话主人，各设备的侧栏马上并入。
- 早于这套记法的对话（`title_source` 为空）不再补概括。
"""
import frappe

from pearnly_core.assistant import config, context, contracts, engine, sidebar

SESSION = context.SESSION
FROM_MESSAGE = "message"  # 临时标题（首条消息原文），等第一轮结束后概括
SUMMARIZING = "summarizing"
SUMMARY = "summary"  # 概括过（成不成都算）
BY_USER = "user"  # 用户手动改过名
JOB = "pearnly_core.assistant.titles.summarize"
JOB_TIMEOUT = 120
FIELDS = ["name", "session_id", "user", "title", "title_source"]


def start(row, text: str, user: str) -> None:
    """首条消息：原文当临时标题。调用方在请求事务里，提交后才推给侧栏。"""
    title = contracts.title_from(text)
    frappe.db.set_value(SESSION, row.name, {"title": title, "title_source": FROM_MESSAGE})
    row.update({"title": title, "title_source": FROM_MESSAGE})
    sidebar.changed(user, "titled", row.session_id, title=title)


def due(row) -> bool:
    """还挂着临时标题、等着概括。"""
    return (row.get("title_source") or "") == FROM_MESSAGE


def request(name: str, after_commit: bool = False) -> None:
    """排一次概括（同一段对话排着、跑着的不重复排；真正只概括一次靠任务里的状态核对）。"""
    frappe.enqueue(JOB, queue="short", timeout=JOB_TIMEOUT, job_id=f"assistant-title-{name}", deduplicate=True,
                   enqueue_after_commit=after_commit, name=name)


def turn_ended(conversation: str) -> None:
    """推送线程看到这段对话的一轮结束了：还挂着临时标题就排概括。"""
    row = frappe.db.get_value(SESSION, {"session_id": conversation}, ["name", "title_source"], as_dict=True)
    if row and due(row):
        request(row.name)


def summarize(name: str, legacy_title: str | None = None) -> None:
    """后台任务：概括这段对话的侧栏标题，写回并推给会话主人。`legacy_title` 只给上线前已排队的旧任务用
    （`sessions.retitle`）：那时还没有 `title_source`，标题仍是排队时的原文才概括。"""
    row = _claim(name, legacy_title)
    if not row:
        return
    title = ""
    model = config.settings().engine.title_model
    if model:
        try:
            api = engine.adapter()
            # 第一段远端会话的编号就是对话编号（接续的新段在 remote_ids 后面），第一轮一定在它里面
            message, reply = contracts.title_parts(api.events(row.session_id))
            message = message or row.title or ""
            title = contracts.make_title(
                lambda too_long: api.summarize(contracts.title_prompt(message, reply, too_long), model))
        except Exception:
            frappe.log_error(title="Assistant title summary failed")
    _finish(row, title)


def _claim(name, legacy_title):
    row = frappe.db.get_value(SESSION, name, FIELDS, as_dict=True, for_update=True)
    legacy = legacy_title is not None and row and not row.title_source and row.title == legacy_title
    if not row or not (due(row) or legacy):
        frappe.db.commit()  # 放开行锁
        return None
    frappe.db.set_value(SESSION, name, "title_source", SUMMARIZING, update_modified=False)
    frappe.db.commit()
    return row


def _finish(row, title: str) -> None:
    now = frappe.db.get_value(SESSION, row.name, ["title", "title_source"], as_dict=True, for_update=True)
    if not now or now.title_source != SUMMARIZING:
        # 删掉了，或用户在概括期间改了名：不动
        frappe.db.commit()
        return
    values = {"title_source": SUMMARY}
    if title:
        values["title"] = title
    frappe.db.set_value(SESSION, row.name, values, update_modified=False)
    frappe.db.commit()
    if title and title != now.title:
        # 推给这段对话的主人（读自本站表），不信任务参数
        sidebar.changed(row.user, "titled", row.session_id, after_commit=False, title=title)
