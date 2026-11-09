"""侧栏标题（ADR-019，同 Claude：主题名词短语）。

- 首条消息原文先当临时标题（`start`，只写本站表，不拖慢发送），记 `title_source = message`。
- 标题 = 用户第一句的简短摘要（主会话 11-07：同 Claude；中文 10 字左右）：第一句发出后马上在后台概括一次（`summarize`），
  只看这一句（原文随任务交过去，不读引擎、不等回复），第一轮还没结束标题就换上。只发了附件的：发出时还不知道附件里是什么，
  先用文件名当临时标题，等第一轮结束（推送线程看到时排，`turn_ended`；没看到的，下一条消息或任一页面在对话空闲时读运行状态
  时补排）按附件名与 CC 第一段回复的开头（从第一段远端会话读）概括。用引擎目录登记的便宜模型，按显示宽度卡长度
  （`contracts.make_title`：超宽重问一次，仍超宽在词或字形簇边界收短）。
- 标题用会话主人的界面语言（`language`），不跟着消息里夹的外文专名走（10-19 测试：中文对话被起成泰文标题）。
- 每段对话只概括一次：任务先在行锁下把状态从 message 改成 summarizing 才调模型，同时排了几次也只有一次调到模型。
  概括不出来（模型出错、没给、照抄提示里的示例标题）保留临时标题，同样算概括过，不再重试。
- 素材只取这段对话自己的：本行的对话编号读首条消息与第一段回复（`contracts.title_parts`），推给本行记的主人；
  不读别的对话，提示里的示例标题照抄回来的不收（2026-10-16 测试：「ภาษีเดือน 9 สามบริษัท」是示例原文，不是别的对话串来的）。
- 用户手动改过名（`title_source = user`，见 `sessions.rename`）就不再自动覆盖：概括期间改了名的，写回前在行锁下核对，
  以用户的名字为准。
- 标题变了（临时标题、概括出的标题）经 `sidebar` 推给会话主人，各设备的侧栏马上并入。
- 早于这套记法的对话（`title_source` 为空）不再补概括。
- 代录会话建的时候还没有用户的话，先放公司名（或任务名）当占位标题，记 `title_source = placeholder`（侧栏三处 11-07：代录、上传
  开出来的对话标题全是公司名）：首条消息发出时同直写一样换成原文、第一轮结束后概括（`sessions.send`）。只发了附件的，概括素材是
  附件名与 CC 第一段回复（`contracts.title_parts`），标题按回复里说的单据内容起。
- 概括期间对话被删了（用户回复一出来就删）：任务静默收手，不记错误、不写回（`_finish` 行没了也不动）；删除本身不等这个任务
  （`sessions._write` 的说明）。概括期间这一行被别的请求改过（发消息、推送线程）：写回前先结束读快照，撞 1020 / 死锁整笔重来。
"""
import frappe

from pearnly_core.assistant import config, context, contracts, engine, sidebar, toolrun
from pearnly_core.assistant.engines import base as engine_base

SESSION = context.SESSION
FROM_MESSAGE = "message"  # 临时标题（首条消息原文），等第一轮结束后概括
SUMMARIZING = "summarizing"
SUMMARY = "summary"  # 概括过（成不成都算）
BY_USER = "user"  # 用户手动改过名
PLACEHOLDER = "placeholder"  # 代录会话建时放的公司名（任务名），首条消息发出时换掉
JOB = "pearnly_core.assistant.titles.summarize"
JOB_TIMEOUT = 120
FIELDS = ["name", "session_id", "user", "title", "title_source", "remote_ids"]


def start(row, text: str, user: str) -> None:
    """首条消息：原文当临时标题。调用方在请求事务里，提交后才推给侧栏。有字的马上排概括（只看这一句，提交后入队）；
    只发了附件的等第一轮结束（`turn_ended`）。"""
    title = contracts.title_from(text)
    frappe.db.set_value(SESSION, row.name, {"title": title, "title_source": FROM_MESSAGE})
    row.update({"title": title, "title_source": FROM_MESSAGE})
    sidebar.changed(user, "titled", row.session_id, title=title)
    if contracts.has_words(text):
        request(row.name, after_commit=True, message=str(text))


def provisional(row) -> bool:
    """还没有用户的话起的标题：空的，或代录会话建时放的占位标题。首条消息发出时用它的原文（`start`）。"""
    return not row.get("title") or (row.get("title_source") or "") == PLACEHOLDER


def due(row) -> bool:
    """还挂着临时标题、等着概括。"""
    return (row.get("title_source") or "") == FROM_MESSAGE


def request(name: str, after_commit: bool = False, message: str | None = None) -> None:
    """排一次概括（同一段对话排着、跑着的不重复排；真正只概括一次靠任务里的状态核对）。``message``：首条消息原文（有字的
    马上概括时随任务交过去，任务不再从引擎读）。"""
    extra = {"message": message} if message is not None else {}
    frappe.enqueue(JOB, queue="short", timeout=JOB_TIMEOUT, job_id=f"assistant-title-{name}", deduplicate=True,
                   enqueue_after_commit=after_commit, name=name, **extra)


def turn_ended(conversation: str) -> None:
    """推送线程看到这段对话的一轮结束了：还挂着临时标题就排概括。"""
    row = frappe.db.get_value(SESSION, {"session_id": conversation}, ["name", "title_source"], as_dict=True)
    if row and due(row):
        request(row.name)


def summarize(name: str, legacy_title: str | None = None, message: str | None = None) -> None:
    """后台任务：概括这段对话的侧栏标题，写回并推给会话主人。``message``：首条消息原文（有字的发出时马上排，只按它概括）；
    没给的从第一段远端会话读首条消息与 CC 第一段回复（只发了附件的，第一轮结束后）。`legacy_title` 只给上线前已排队的
    旧任务用（`sessions.retitle`）：那时还没有 `title_source`，标题仍是排队时的原文才概括。"""
    row = _claim(name, legacy_title)
    if not row:
        return
    title = ""
    model = config.settings().engine.title_model
    if model:
        try:
            api = engine.adapter()
            if message is not None and contracts.has_words(message):
                message, reply = contracts.title_parts([_said(message)])
            else:
                # 第一轮在第一段远端会话里（直写是对话编号本身，代录记在 remote_ids 的第一个）
                events = _opening(api.events(_first_remote(row)))
                message, reply = contracts.title_parts(events)
                if not reply and not contracts.has_words(contracts.first_message(events)):
                    _release(row)  # 只发了附件、还没有回复：等第一轮结束再概括（不算概括过）
                    return
            message = message or row.title or ""
            target = language(row)
            title = contracts.make_title(
                lambda too_long: api.summarize(contracts.title_prompt(message, reply, too_long, target), model),
                source=message, examples=engine_base.TITLE_EXAMPLES)  # 只按用户首句判断是否照抄示例
        except Exception:
            if _gone(name):
                return  # 概括期间对话被删了（远端会话跟着删、读不到）：静默收手，不记错误（11-05 删对话竞争）
            frappe.log_error(title="Assistant title summary failed")
    _finish(row, title)


def _said(text) -> dict:
    return {"type": "user.message", "content": [{"type": "text", "text": str(text)}]}


def _opening(events) -> list:
    """开头的事件：读到第一条用户消息之后的第一段回复就停（引擎按页读，不读整段对话）。"""
    out, asked = [], False
    for event in events:
        out.append(event)
        kind = event.get("type")
        asked = asked or kind == "user.message"
        if asked and kind == "agent.message":
            break
    return out


def _first_remote(row) -> str:
    """第一段远端会话（同 `sessions.segments` 的第一个；titles 不回头导入 sessions）。"""
    import json

    try:
        found = json.loads(row.get("remote_ids") or "[]")
    except ValueError:
        found = []
    return found[0] if found else row.session_id


def _release(row) -> None:
    """认领了却还不能概括（只发了附件、第一轮还没回复）：放回临时标题，第一轮结束时再排。"""
    frappe.db.set_value(SESSION, {"name": row.name, "title_source": SUMMARIZING}, "title_source", FROM_MESSAGE,
                        update_modified=False)
    frappe.db.commit()


def _gone(name) -> bool:
    """这段对话在本站已经删掉了。先结束任务的读快照（`_claim` 已提交，这里没有要丢的写），看的是此刻的表。"""
    frappe.db.rollback()
    return not frappe.db.exists(SESSION, name)


def language(row) -> str:
    """标题用的语言（`toolrun.interface_language`，与 CC 回复的语言、这段对话里工具结果的语言同一来源）：用户发消息时
    对话页的界面语言（`toolrun.remember_lang` 记下的）；没记着（过期、缓存清了）用会话主人的账号语言；都不认得用英文。
    后台任务没有请求和设备，不能按 `preferences.interface_language` 现取。"""
    return toolrun.interface_language(row.session_id, row.user)


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
    """写回标题。`_claim` 提交后读语言、调模型的这几秒里，发消息 / 推送线程可能改了这一行并提交：先结束本任务的读快照
    （这期间只读、没有要丢的写）再加锁读，撞上 1020 / 死锁按 `transaction.conflicts` 整笔重来；用尽只记一条错误、标题留原文
    （11-09 部署后 e2e 两条 1020，栈都在这里）。行没了（删掉了）静默收手。"""
    from pearnly_core.transaction import conflicts

    frappe.db.rollback()
    try:
        conflicts.retried(_write_back, row, title)
    except (conflicts.Exhausted, conflicts.RollbackFailed):
        frappe.log_error(title="Assistant title write busy")


def _write_back(row, title: str) -> None:
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
