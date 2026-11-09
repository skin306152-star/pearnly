"""对话里的文件（ADR-019）：附件进会话、结果文件列出与下载。

- 附件：浏览器传到本站 → 校验类型与大小（HEIC 照片先转 JPEG，与三个产品的上传口一致）→ 交给引擎放进该会话，
  智能体按返回的位置读取。字节传引擎在线程里与站点这边的活同时做（`_upload`，11-09 U2）。
- 结果：智能体产出的文件按会话列出，可预览、下载。Excel 与 Word（.docx）由这里读成纯数据交给结果面板（`preview`），
  页面按文字渲染，文档里的脚本、外链到不了页面；读不出就给下载。
- 文件不脱离会话存在：下载前先核对「会话属于当前用户」且「文件属于这个会话」，猜编号读不到别人的文件。
- 附件原件在本站也留一份：引擎的文件库不让下载用户上传的文件，产品工具要用原件（例如 Cowork 把对话里发来的
  票据交给录入工作台识别）只能从这里取，见 `attachment`。存在站点私有目录 `private/assistant-attachments/<会话>/`
  （不进 File 表、没有下载地址）：同一张票识别过以后，它在 File 表里是受保护的原件，再存一份同内容的 File 会被
  Cowork 的原件守卫拒绝（`pearnly_cowork/files/guard.py`，2026-10-10 测试：第二次发同一张票上传失败）。删对话时一并删。
- 工具放进对话的文件也在本站留一份（2026-10 CC 闭环 L3）：工具产出（复核工作簿、回读的总账 / 税报……）能再交给别的
  工具当输入（例如对账），产品从文件夹取来的文件同样。来源分三种（`origin`）：用户上传（会话目录本身）、工具生成
  （`tool/`）、从文件夹取来（`folder/`）；产品据此拒绝把自己生成的文件再拿去识别。
- 对话接续到新上下文后，沙箱里的旧文件没了，本站这份还在（按对话留，不分段）：`kept` 列给智能体，工具照样按文件名取。
- 用户发的附件也能在右侧面板预览（2026-10-16 CC 外壳，同 Claude 点消息里的附件卡片）：引擎不让下载用户上传的文件，
  `download` / `preview` 找不到可下载的结果时取本站留存的那份；列表里的附件带上大小与 PDF 页数（附件卡片的说明行）。
- 代录会话里放进输入框的文件（2026-11-06）：同一个上传口、同一套附件组件，另交给会话挂的代录任务识别（``_operator_take``）。
- PDF 的一页画成图（`page_image`，第 5 轮测试 1、2）：附件卡片的缩略图（第一页，`thumb`）、手机上的 PDF 预览（安卓 Chrome
  不能在 iframe 里显示 PDF，逐页 `page`）。用镜像里已有的 PyMuPDF（识别流水线同一个，frappe.Dockerfile 锁定），不给前端
  加 PDF 渲染库；只有两种宽度，回应带私有缓存头（文件编号对应的内容不变），浏览器同一张只取一次。画好的页图与从引擎取来的
  结果 PDF 原件留在本对话目录的 `rendered/` 里（随对话删），同一页各设备只画一次、引擎的结果只取一次（第 5 轮审查）。
"""
import io
import mimetypes
import os
import re
import shutil
import threading
import time
import uuid

import frappe
from frappe import _

from pearnly_core.assistant import contracts, docx_preview, engine, sessions
from pearnly_core.desk import heic

# 只有这些类型可以在本站内联打开。网页、SVG 一律按下载给出：它们在本站域名下打开会执行脚本；
# 对话页的预览把网页放进无同源权限的沙箱 iframe 里显示（前端读文本后用 srcdoc）。
INLINE_TYPES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp", "application/pdf"})


# 同一段代录会话里同时传几份（一次选两份）按会话排队：等前一份传完、提交了再做下一份
OPERATOR_UPLOAD_LOCK_SECONDS = 180


def upload(session_id) -> dict:
    row = sessions.owned(session_id)
    if not row.get("operator_task"):
        return _upload(row)
    # 代录会话：第一份要开远端会话（`sessions._remote_ready`），每份都要记到同一个任务上；几份并发时第二份读到的是旧样子，
    # 写回撞「记录已被改」（11-06 测试机：一次选两份，第二份 500 QueryDeadlockError）。按会话排队，拿到锁后丢掉此前的读视图
    # 重新读，做完先提交再放锁，下一份读到的就是提交后的样子。
    with frappe.cache.lock(f"{frappe.local.site}:pearnly_assistant:upload:{row.name}", timeout=OPERATOR_UPLOAD_LOCK_SECONDS,
                           blocking_timeout=OPERATOR_UPLOAD_LOCK_SECONDS):
        frappe.db.rollback()
        out = _upload(sessions.owned(session_id))
        frappe.db.commit()
        return out


def _upload(row) -> dict:
    """一份附件进会话。字节传引擎（引擎在美国，10MB 约 2–5 秒）放在线程里，与站点这边的活同时做：开 / 接续远端会话、
    代录交任务（存原件 + 交识别）、本站留存、读页数；两边都做完再挂进会话。11-08 晚 24 页 10.6MB 一份串着做要 13.4 秒
    （研究 docs/work/2026-11-08-cc-upload-ux-研究.md，U2）。上传编号由本站先定（`_new_upload_id`），交任务、留存、
    页数缓存不必等引擎回编号；挂载位置里记的也是它，会话文件列表、消息里的附件、工具取文件都按它认，与以前引擎编号的老附件同一认法。
    每段耗时记进日志 `pearnly.assistant.upload`（U0：不带文件名、票面）。"""
    steps = _Steps()
    sent = (frappe.request.files or {}).get("file") if frappe.request else None
    if not sent:
        frappe.throw(_("No file was received."))
    content = sent.stream.read(contracts.UPLOAD_MAX_BYTES + 1)
    name = sessions._guard(contracts.upload_name, sent.filename, len(content))
    mime = sent.mimetype or "application/octet-stream"
    if heic.is_heic(content):
        content, name, mime = heic.to_jpeg(content), heic.jpeg_name(name), "image/jpeg"
    steps.done("read")
    api = engine.adapter()
    upload_id = _new_upload_id()
    sending = _Background(lambda: api.upload(name, content, mime))
    remote = sessions.segment_for_turn(row, api)
    steps.done("session")
    task = _operator_take(row, upload_id, name, content)
    steps.done("task")
    _keep(row, upload_id, name, content)
    meta = _remember_meta(upload_id, name, content)
    steps.done("keep")
    try:
        engine_file = sending.result()
    except Exception:
        # 引擎一时没收（断线、超时）：站点这边已交过任务、留存过，同一个上传编号再传一次，不让用户重传（重传会再交一次识别）
        frappe.log_error(title="Assistant attachment upload retried")
        engine_file = api.upload(name, content, mime)
    steps.done("engine_wait", engine=sending.seconds)
    stored = api.mount(remote, engine_file, upload_id, name)
    steps.done("mount")
    steps.log(size=len(content), pages=meta.get("pages"), operator=bool(row.get("operator_task")))
    return {"fileId": stored["file_id"], "filename": name, "mountPath": stored["path"], **meta, **task}


def _new_upload_id() -> str:
    """用户附件的上传编号：本站先定，挂载位置、本站留存、交任务的请求键都用它（`contracts.file_id` 认得的写法）。"""
    return "upl_" + uuid.uuid4().hex


class _Background:
    """在线程里跑一件只做网络调用的事（不碰站点库、`frappe.local` 在线程里是空的），`result()` 等它做完、原样抛它的错。"""

    def __init__(self, work):
        self._work, self._value, self._error, self.seconds = work, None, None, 0.0
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        began = time.monotonic()
        try:
            self._value = self._work()
        except BaseException as exc:  # noqa: BLE001 —— 原样交给 result() 抛
            self._error = exc
        finally:
            self.seconds = time.monotonic() - began

    def result(self):
        self._thread.join()
        if self._error is not None:
            raise self._error
        return self._value


class _Steps:
    """上传口每段耗时（秒）：`done(段名)` 记上一段到此刻，`log` 写一行进 `pearnly.assistant.upload`（U0）。"""

    def __init__(self):
        self._began = self._last = time.monotonic()
        self.marks = {}

    def done(self, step, **extra):
        now = time.monotonic()
        self.marks[step] = round(now - self._last, 3)
        self.marks.update({key: round(value, 3) for key, value in extra.items()})
        self._last = now

    def log(self, **facts):
        total = round(time.monotonic() - self._began, 3)
        parts = [f"{key}={value}" for key, value in {**facts, **self.marks, "total": total}.items() if value is not None]
        try:
            frappe.logger("pearnly.assistant.upload").info("attachment upload " + " ".join(parts))
        except Exception:
            pass


def _operator_take(row, file_id, name, content) -> dict:
    """代录会话（2026-11-06「选中就上传」）：放进输入框的这一份同时交给会话挂的任务识别，请求键就是文件编号（带着它的消息发出时
    按它记上时刻，``express_operator.uploads.sent``）。回包多带 ``task`` 与 ``upload``（同 ``task_uploads`` 里的一项），输入框那一项
    按它显示识别中；产品不收上传时不带，文件照常只进对话。"""
    if not row.get("operator_task"):
        return {}
    from pearnly_core.express_operator import uploads

    view = uploads.take(row.operator_task, name, content, file_id)
    return {"task": row.operator_task, "upload": view} if view else {}


def taken(file_id) -> str:
    """当前工具调用所在的代录会话里，这一份（已认成编号的 ``file_id``）放进输入框时交给任务的那次识别（识别任务号，
    `_operator_take`）；不是代录会话、这一份没交过回空串。产品的「识别附件」工具据此拿回那一份，不再按自己的请求号另交一次
    （11-07 本机实验栈真引擎：代录里同一份 12 页 PDF 识别两次，照收两次费、资料里 11 张列成 22 行，CC 等的是排在后面的自己那份）。"""
    context = getattr(frappe.local, "pearnly_assistant", None) or {}
    row = sessions.owned(context.get("session_id"), allow_archived=True)
    if not row.get("operator_task"):
        return ""
    from pearnly_core.express_operator import uploads

    return uploads.batch_of(row.operator_task, file_id)


KEPT = "assistant-attachments"
# 用户上传以外的两种来源，各占会话目录下的一个子目录：工具生成的、产品从文件夹取来的。
ORIGINS = ("tool", "folder")
USER = "user"


def _folder(session_name: str, origin: str = USER) -> str:
    base = frappe.get_site_path("private", KEPT, re.sub(r"[^A-Za-z0-9_-]", "_", session_name))
    return base if origin == USER else os.path.join(base, origin)


def _keep(row, file_id, name, content, origin=USER) -> None:
    folder = _folder(row.name, origin)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{file_id}--{os.path.basename(name)}")
    with open(path + ".part", "wb") as handle:
        handle.write(content)
    os.replace(path + ".part", path)


def forget(session_name: str) -> None:
    """删对话时把留存的附件一起删（提交以后再删，回滚时不丢）。"""
    folder = _folder(session_name)
    frappe.db.after_commit.add(lambda: shutil.rmtree(folder, ignore_errors=True))


def _read(path: str) -> bytes:
    with open(path, "rb") as handle:
        return handle.read()


def _kept(file_id=None, row=None) -> list[tuple[str, callable, str]]:
    """[(「<编号>--<文件名>」, 取内容, 来源 user / tool / folder)]，旧 → 新：先是 2026-10-10 晚以前存在 File 表里的旧留存
    （都是用户上传），再是私有目录（按放进来的先后）。不给 `row` 就是当前工具调用所在的对话。"""
    if row is None:
        context = getattr(frappe.local, "pearnly_assistant", None) or {}
        row = sessions.owned(context.get("session_id"), allow_archived=True)
    found = []
    filters = {"attached_to_doctype": sessions.SESSION, "attached_to_name": row.name}
    if file_id:
        filters["file_name"] = ["like", file_id + "--%"]
    for old in frappe.get_all("File", filters=filters, fields=["name", "file_name"], order_by="creation asc"):
        found.append((old.file_name, lambda name=old.name: frappe.get_doc("File", name).get_content(), USER))
    local = []
    for origin in (USER,) + ORIGINS:
        folder = _folder(row.name, origin)
        if not os.path.isdir(folder):
            continue
        for entry in os.listdir(folder):
            path = os.path.join(folder, entry)
            if ("--" in entry and not entry.endswith(".part") and (not file_id or entry.startswith(file_id + "--"))
                    and os.path.isfile(path)):
                local.append((os.path.getmtime(path), entry, path, origin))
    return found + [(entry, lambda path=path: _read(path), origin) for _at, entry, path, origin in sorted(local)]


def attachments() -> list[dict]:
    """当前工具调用所在对话里留存的文件：[{file_id, filename}]（工具拿错编号时据此告诉智能体有哪些）；不是用户上传的
    另带 `origin`（tool / folder）。"""
    out = []
    for entry, _read, origin in _kept():
        file_id, sep, filename = entry.partition("--")
        if sep and not any(item["file_id"] == file_id for item in out):
            out.append({"file_id": file_id, "filename": filename, **({} if origin == USER else {"origin": origin})})
    return out


def kept(row) -> list[dict]:
    """这段对话（`row`：会话记录）留存的全部文件，新 → 旧：[{file_id, filename, origin}]。接续新上下文时告诉智能体
    还有哪些文件能交给工具（`sessions.segment_for_turn`）：沙箱里的那份随旧上下文没了，本站这份还在、按文件名照样取得到。"""
    out = []
    for entry, _read, origin in reversed(_kept(row=row)):
        file_id, sep, filename = entry.partition("--")
        if sep:
            out.append({"file_id": file_id, "filename": filename, "origin": origin})
    return out


def add_to_conversation(filename: str, content: bytes, mime: str, *, origin: str = "tool") -> str:
    """工具生成的文件（例如 Cowork 导出的复核工作簿）放进当前对话，返回智能体能读到的位置；它再放进结果目录给用户。

    同时在本站留一份（`origin`：tool = 工具生成，folder = 产品从已授权文件夹取来），之后的工具能像用户附件一样经
    `attachment` 取用（对账、盘点……）。类型与大小同用户上传（`contracts.upload_name`），HEIC 照片同样先转 JPEG。"""
    if origin not in ORIGINS:
        raise ValueError("unknown origin")
    context = getattr(frappe.local, "pearnly_assistant", None) or {}
    row = sessions.owned(context.get("session_id"))
    name = sessions._guard(contracts.upload_name, filename, len(content))
    if heic.is_heic(content):
        content, name, mime = heic.to_jpeg(content), heic.jpeg_name(name), "image/jpeg"
    api = engine.adapter()
    stored = api.attach(sessions.current(row), name, content, mime)
    _keep(row, stored["file_id"], name, content, origin)
    return stored["path"]


def _resolve(ref) -> str:
    """模型给的可能是编号，也可能是沙箱里的挂载路径（`/mnt/session/uploads/<编号>/<文件名>`）或文件名
    （2026-10-10 测试：先传路径被拒、再传编号，多绕一轮）：按本对话的附件认成编号；认不出原样返回。"""
    text = str(ref or "").strip()
    known = attachments()
    ids = {item["file_id"] for item in known}
    if text in ids:
        return text
    parts = [part for part in text.split("/") if part]
    for part in parts:
        if part in ids:
            return part
    name = parts[-1] if parts else text
    named = [item["file_id"] for item in known if item["filename"] == name]
    return named[-1] if named else text


def resolve(ref) -> str:
    """模型给的编号、挂载路径或文件名 → 本对话里那个文件的编号（同名取最新的）。工具按它定幂等键：同一个文件不论
    怎么称呼都是同一件请求；接续新上下文后智能体按文件名称呼旧文件，也不会被当成另一份、也不会与别的对话里的同名文件撞。
    不在这个对话里报与 `attachment` 相同的错。"""
    fid = sessions._guard(contracts.file_id, _resolve(ref))
    if not _kept(fid):
        frappe.throw(_("This attachment is not in this conversation."))
    return fid


def attachment(file_id) -> tuple[str, bytes]:
    """当前工具调用所在对话里的一个文件（文件名, 内容）：用户附件、工具生成的、从文件夹取来的都算。只认这个对话、这个人的，
    猜编号拿不到别人的。编号、挂载路径、文件名都认（`_resolve`）。"""
    fid = sessions._guard(contracts.file_id, _resolve(file_id))
    for entry, read, _origin in _kept(fid):
        if entry.startswith(fid + "--"):
            return entry[len(fid) + 2:], read()
    frappe.throw(_("This attachment is not in this conversation."))


def origin(file_id) -> str:
    """这个文件从哪来：user（用户上传）/ tool（工具生成）/ folder（从已授权文件夹取来）。认法与 `attachment` 相同，
    不在这个对话里就报同样的错。"""
    fid = sessions._guard(contracts.file_id, _resolve(file_id))
    for entry, _read, source in _kept(fid):
        if entry.startswith(fid + "--"):
            return source
    frappe.throw(_("This attachment is not in this conversation."))


def listing(session_id) -> list[dict]:
    """本会话的全部文件：挂进去的附件（只作输入）+ 智能体产出的结果（可下载）。按生成时间从早到晚排：引擎列文件是
    新的在前，结果面板默认打开最后一个，新导出的文件出来时面板却停在最早那份（2026-10-10 测试）。
    附件（kind = upload）按本站留存的那份补上大小与 PDF 页数（`pages`），对话里的附件卡片显示「PDF · N 页」/ 大小。
    附件的编号是挂载位置里的那个（`upload` 返回、消息的挂载位置里记的、本站留存用的同一个：11-09 起是本站先定的上传编号，
    更早的是引擎给的文件编号，认法相同；引擎另存的副本不列，见适配器 `files`），刷新后、别的设备上卡片照样按它对上（第 5 轮测试 1）。"""
    row = sessions.owned(session_id, allow_archived=True)
    api = engine.adapter()
    found = sorted((f for remote in sessions.segments(row) for f in api.files(remote)),
                   key=lambda f: str(f.get("created_at") or ""))
    for f in found:
        if f.get("kind") == "upload":
            f.update(_upload_meta(row, f["id"]))
    return found


# 附件的大小与页数按文件编号记一份（引擎的文件编号全局唯一、内容不变）：列表每次打开对话、每轮跑完都要取，不每次读原件。
# 本站没留存的附件（很早以前的）也记一次「没有」，不每次重扫目录；只记一小时，上传时正好撞上列表的那一下不会久留。
META_CACHE = "pearnly_assistant:upload-meta:"
META_TTL = 7 * 24 * 3600
META_MISSING_TTL = 3600


def _pdf_pages(content: bytes) -> int | None:
    try:
        import pypdf

        return len(pypdf.PdfReader(io.BytesIO(content)).pages) or None
    except Exception:
        return None


def _remember_meta(fid: str, name: str, content: bytes) -> dict:
    """附件的说明数据：{"size_bytes"}，PDF 另带 {"pages"}（读不出页数就不带）。记进缓存，列表时不再读原件。"""
    meta = {"size_bytes": len(content)}
    if str(name).lower().endswith(".pdf"):
        pages = _pdf_pages(content)
        if pages:
            meta["pages"] = pages
    try:
        frappe.cache.set_value(META_CACHE + fid, meta, expires_in_sec=META_TTL)
    except Exception:
        pass
    return meta


def _upload_meta(row, fid: str) -> dict:
    """列表里一个附件的大小与页数：先看缓存，没有就读本站留存的那份；没留存（很早以前的附件）就不补。"""
    try:
        cached = frappe.cache.get_value(META_CACHE + fid)
    except Exception:
        cached = None
    if isinstance(cached, dict):
        return cached
    for entry, read, _origin in _kept(fid, row=row):
        if entry.startswith(fid + "--"):
            try:
                return _remember_meta(fid, entry[len(fid) + 2:], read())
            except OSError:
                return {}
    try:
        frappe.cache.set_value(META_CACHE + fid, {}, expires_in_sec=META_MISSING_TTL)
    except Exception:
        pass
    return {}


def _stored(row, api, fid) -> tuple[dict, callable]:
    """本对话里能打开的文件：(说明, 取内容)。先找本站留存的那份（用户上传的附件，以及工具放进对话的文件；只读本地、
    只认这个对话）；没有再去引擎里找可下载的结果（任一段，每段两次引擎接口调用）——引擎不让下载用户上传的文件，
    附件卡片的缩略图、预览先走引擎会白白多付这几次调用（审查第 4 轮：一条消息几十张照片就是几十轮）。
    两处都没有就当作不存在，猜编号拿不到别的对话、别人的文件。"""
    for entry, read, _origin in _kept(fid, row=row):
        if entry.startswith(fid + "--"):
            name = entry[len(fid) + 2:]
            mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
            return {"id": fid, "filename": name, "mime_type": mime, "size_bytes": None, "local": True}, read
    for remote in sessions.segments(row):
        for f in api.files(remote):
            if f["id"] == fid and f["downloadable"]:
                return f, lambda remote=remote: api.read_file(remote, fid)
    frappe.throw(_("File not found"), frappe.DoesNotExistError)


def download(session_id, file_id, inline=False) -> None:
    row = sessions.owned(session_id, allow_archived=True)
    fid = sessions._guard(contracts.file_id, file_id)
    api = engine.adapter()
    meta, read = _stored(row, api, fid)
    frappe.local.response.filename = meta["filename"]
    frappe.local.response.filecontent = read()
    # Frappe 的 binary 回应是「附件 + octet-stream」：图片用 <img> 照样显示，PDF 放进结果面板却成了下载、一片空白
    # （2026-10-10 测试，桌面端每打开一次还多存一份）。PDF 用 Frappe 的 pdf 回应（inline + application/pdf）。
    if inline and meta["mime_type"] == "application/pdf" or inline and str(meta["filename"]).lower().endswith(".pdf"):
        frappe.local.response.type = "pdf"
    else:
        frappe.local.response.type = "binary" if inline and meta["mime_type"] in INLINE_TYPES else "download"


# 结果面板的结构化预览：表格只读前几张表的前若干行列；Word 只读正文（上限见 docx_preview）。
# 单个文件超过上限就不预览（下载照常）。
PREVIEW_MAX_BYTES = 5 * 1024 * 1024
PREVIEW_SHEETS = 10
PREVIEW_ROWS = 500
PREVIEW_COLS = 40
SHEET_TYPES = (".xlsx", ".xlsm")
DOCUMENT_TYPES = (".docx",)


def preview_kind(filename) -> str | None:
    """按文件名定结构化预览的类型：sheets（Excel）/ document（Word .docx）；别的类型没有结构化预览。
    老格式 .doc / .xls 是二进制，不读。"""
    name = str(filename or "").strip().lower()
    if name.endswith(SHEET_TYPES):
        return "sheets"
    if name.endswith(DOCUMENT_TYPES):
        return "document"
    return None


def preview(session_id, file_id) -> dict:
    """结果文件的结构化预览：Excel `{"type": "sheets", "sheets": [{"name", "rows"}]}`；Word
    `{"type": "document", "blocks": [...]}`（块的写法见 `docx_preview.read`）；PDF `{"type": "pdf", "pages": N}`
    （页面按页取 `page_image`，手机上用）；其余类型、超过上限或读不出 `{"type": "none"}`，页面给下载。"""
    row = sessions.owned(session_id, allow_archived=True)
    fid = sessions._guard(contracts.file_id, file_id)
    meta, read = _opened(row, fid)
    if _is_pdf(meta):
        pages = _cached(PAGES_CACHE + fid)
        if not isinstance(pages, int):
            content = _pdf_content(row, fid, meta, read)
            pages = _pdf_pages(content) if content else None
            _remember(PAGES_CACHE + fid, pages or 0, META_TTL if pages else META_MISSING_TTL)
        return {"type": "pdf", "pages": pages} if pages else {"type": "none"}
    kind = preview_kind(meta["filename"])
    if not kind or int(meta.get("size_bytes") or 0) > PREVIEW_MAX_BYTES:
        return {"type": "none"}
    content = read()
    if len(content) > PREVIEW_MAX_BYTES:
        return {"type": "none"}
    if kind == "sheets":
        return {"type": "sheets", "sheets": sheets(content)}
    try:
        blocks = docx_preview.read(content)
    except docx_preview.Unreadable:
        return {"type": "none"}
    return {"type": "document", "blocks": blocks} if blocks else {"type": "none"}


def _is_pdf(meta) -> bool:
    return meta.get("mime_type") == "application/pdf" or str(meta.get("filename") or "").lower().endswith(".pdf")


# PDF 一页画成图：附件卡片缩略图（卡片宽 120 的两倍）、整页（手机屏宽的三倍上下）。只收这两种，缓存不碎、也不能要一张超大图。
PAGE_WIDTHS = {"thumb": 240, "page": 1240}
# 页面太长（长条收据）时高最多是宽的这么多倍，免得一张图几十兆像素
PAGE_TALLEST = 6
PAGE_MAX = 500
# 页数、画页只读这么大以内的 PDF（上传上限 30MB；工具产出的结果可能更大，太大的给下载）
PDF_MAX_BYTES = 60 * 1024 * 1024
# 同一个文件编号的内容不变：浏览器留一周，同一张缩略图 / 同一页只取一次（只给本人的浏览器，不进共享缓存）
PAGE_CACHE = "private, max-age=604800"
# 含图的页（拍照、扫描的单据：本产品最常见的 PDF）编 JPEG，纯文字 / 矢量页编 PNG（字更清楚、也更小）（第 5 轮审查：
# 照片页编 PNG 一页几 MB，手机流量和服务端编码都吃力）
JPEG_QUALITY = 80
# 画好的页图、从引擎取来的结果 PDF 原件，留在这个对话的私有目录的这个子目录里（`_kept` 不扫子目录里的文件，不进附件
# 列表；删对话时随 `forget` 一起删）。第 5 轮审查：每看一页都去引擎列文件、整份下载再整份解析，新设备打开每张缩略图
# 都重画一次；有了这份，引擎的结果只取一次，同一页各设备只画一次，留存在本对话目录里也就证明了它属于这个对话。
RENDERED = "rendered"
PAGES_CACHE = "pearnly_assistant:pdf-pages:"
IMAGE_TYPES = {"png": "image/png", "jpg": "image/jpeg"}


def render_page(content: bytes, number: int, width: int) -> tuple[bytes, str] | None:
    """PDF 第 ``number`` 页画成宽 ``width`` 的图：(字节, 扩展名 png / jpg)；页码超出、加密、读不出回 None。"""
    try:
        import pymupdf
    except ImportError:
        return None
    try:
        with pymupdf.open(stream=content, filetype="pdf") as document:
            if document.needs_pass or not 1 <= number <= document.page_count:
                return None
            page = document[number - 1]
            box = page.rect
            if box.width <= 0 or box.height <= 0:
                return None
            scale = min(width / box.width, width * PAGE_TALLEST / box.height)
            pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
            if page.get_images(full=False):
                return pixmap.tobytes("jpeg", jpg_quality=JPEG_QUALITY), "jpg"
            return pixmap.tobytes("png"), "png"
    except Exception:  # 坏文件、不支持的写法：PyMuPDF 抛的类型不一（FileDataError / RuntimeError / ValueError…），一律当画不出
        return None


def _cached(key):
    try:
        return frappe.cache.get_value(key)
    except Exception:
        return None


def _remember(key, value, ttl) -> None:
    try:
        frappe.cache.set_value(key, value, expires_in_sec=ttl)
    except Exception:
        pass


def _rendered(row, name: str) -> str:
    return os.path.join(_folder(row.name), RENDERED, name)


def _save(path: str, content: bytes) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".part", "wb") as handle:
            handle.write(content)
        os.replace(path + ".part", path)
    except OSError:
        pass  # 存不下只是下次再取再画


def _opened(row, fid) -> tuple[dict, callable]:
    """同 `_stored`，另认本对话目录里留下的引擎结果 PDF 原件：有这份就不再去引擎列文件、下载。"""
    source = _rendered(row, fid + ".pdf")
    if os.path.isfile(source):
        return ({"id": fid, "filename": fid + ".pdf", "mime_type": "application/pdf",
                 "size_bytes": os.path.getsize(source), "local": True}, lambda: _read(source))
    return _stored(row, engine.adapter(), fid)


def _pdf_content(row, fid, meta, read) -> bytes | None:
    """一个 PDF 的内容（画页、数页数用）；太大回 None。从引擎取来的结果在本对话目录留一份原件（`_opened` 下次读它）。"""
    if int(meta.get("size_bytes") or 0) > PDF_MAX_BYTES:
        return None
    content = read()
    if len(content) > PDF_MAX_BYTES:
        return None
    if not meta.get("local"):
        _save(_rendered(row, fid + ".pdf"), content)
    return content


def _image(body: bytes, ext: str):
    from werkzeug.wrappers import Response

    return Response(body, mimetype=IMAGE_TYPES[ext],
                    headers={"Cache-Control": PAGE_CACHE, "X-Content-Type-Options": "nosniff"})


def page_image(session_id, file_id, page=1, size="thumb"):
    """本对话里一个 PDF 的一页（含图的页 JPEG，否则 PNG）：附件卡片的缩略图（第一页、thumb）、手机上的 PDF 预览
    （逐页、page）。文件的取法、认法同 `download`（只认这个对话的，先读本站留存的那份）；不是 PDF、页码不对、画不出都当作
    不存在。画好的存在本对话目录里，同一页再取（别的设备、刷新）直接给；列表记过「本站没留存」的老附件（引擎也不给下载
    用户上传的文件）直接当不存在，不再去引擎白问一趟。"""
    row = sessions.owned(session_id, allow_archived=True)
    fid = sessions._guard(contracts.file_id, file_id)
    width = PAGE_WIDTHS.get(str(size or ""))
    try:
        number = int(page)
    except (TypeError, ValueError):
        number = 0
    if not width or not 1 <= number <= PAGE_MAX:
        frappe.throw(_("File not found"), frappe.DoesNotExistError)
    name = f"{fid}--{number}-{size}"
    for ext in IMAGE_TYPES:
        path = _rendered(row, f"{name}.{ext}")
        if os.path.isfile(path):
            return _image(_read(path), ext)
    if _cached(META_CACHE + fid) == {}:
        frappe.throw(_("File not found"), frappe.DoesNotExistError)
    meta, read = _opened(row, fid)
    content = _pdf_content(row, fid, meta, read) if _is_pdf(meta) else None
    drawn = render_page(content, number, width) if content else None
    if not drawn:
        frappe.throw(_("File not found"), frappe.DoesNotExistError)
    body, ext = drawn
    _save(_rendered(row, f"{name}.{ext}"), body)
    return _image(body, ext)


def sheets(content: bytes) -> list[dict]:
    import io

    from openpyxl import load_workbook

    values = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    formulas = load_workbook(io.BytesIO(content), read_only=True, data_only=False)
    out = []
    for sheet in values.worksheets[:PREVIEW_SHEETS]:
        raw = formulas[sheet.title].iter_rows(max_row=PREVIEW_ROWS, max_col=PREVIEW_COLS)
        rows = [[_cell(v, f) for v, f in zip(vrow, frow)]
                for vrow, frow in zip(sheet.iter_rows(max_row=PREVIEW_ROWS, max_col=PREVIEW_COLS), raw)]
        while rows and not any(rows[-1]):
            rows.pop()
        width = max((max((i + 1 for i, c in enumerate(r) if c), default=0) for r in rows), default=0)
        out.append({"name": sheet.title, "rows": [r[:width] for r in rows]})
    return out


def _cell(value_cell, formula_cell) -> str:
    """单元格显示文字：按数字格式粗略排版；没有缓存值的公式显示公式本身。"""
    import datetime

    value = getattr(value_cell, "value", None)
    if value is None:
        formula = getattr(formula_cell, "value", None)
        return formula if isinstance(formula, str) and formula.startswith("=") else ""
    fmt = str(getattr(value_cell, "number_format", "") or "")
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        if "0.00" in fmt:
            return f"{value:,.2f}" if "," in fmt else f"{value:.2f}"
        if "#,##0" in fmt:
            return f"{value:,.0f}"
        return f"{value:g}" if isinstance(value, float) else str(value)
    if isinstance(value, datetime.datetime):
        return value.strftime("%Y-%m-%d %H:%M") if (value.hour or value.minute) else value.strftime("%Y-%m-%d")
    if isinstance(value, datetime.date):
        return value.isoformat()
    return str(value)
