# -*- coding: utf-8 -*-
"""Tomo 窗口里对话页和本机之间的桥（QWebChannel，对象名 tomoDesktop）。

对话页的设置（「此电脑」：连接、系统）经这里读写本机设置（desktop_settings.py）；文件夹选择框由这里弹本机原生的。
只在 Tomo 自己的对话页（*.pearnly.com/assistant）上应答，登录页等其他页面调用一律拒绝。

公司的文件夹（2026-10-15，CC 按公司分组）：``pick_folder`` 不收任何路径（网页传来的一概不看），在界面线程弹原生
选择框，选好后在后台判定（desktop_settings.pick_folder），只回绑定（读者、根目录编号、相对路径、显示名…），由页面交给
服务端。另要求对话页的站点就是这台电脑连着的那家公司（``page_url``），否则回 ``other_site``。
``state()`` 带 ``features: ["company_folders", "devices"]``（旧版没有，页面据此降级）与 ``legacy_roots``（设置页的旧授权清理行）。

MR.ERP 登录（ADR-026 第 7 条，11-02；11-23 用户「第一个弹窗不行吗」）：``mrerp_authorize`` 收站点发给对话页的一次性票和
「连接 MR.ERP」弹窗里输的用户名、密码，经机器通道交给站点、站点收下了再存本机凭据管理器（mrerp_login.py）；只回成没成，
不回显、不写日志。旧对话页只带票：照旧弹原生小窗收。

原生小窗（Express 授权登录、MR.ERP 代录登录、上面的旧页面兜底）一律经 ``ask_login`` 弹：托盘里请 CC 窗口进程弹（以 CC 窗口
为父、模态、置前，``WindowHost.login``，11-23 用户：小窗落在 CC 后面看不到），同选文件夹的 ``pick``。

多台电脑（ADR-021，1.7.0）：``state()`` 另带 ``device: {key, name}``（这台电脑的设备密钥与电脑名；取不到是空对象），对话页每次
打开都带它调 ``companion.api.register``（对话页一侧在第 6 批）；成员带 ``device_state``（``superseded`` / ``claimed_elsewhere``：
这把密钥不再用，对话页重新登记，前者带 ``rekey``）。设备密钥只交给本站自己的对话页（``allowed``），不进任何地址栏参数。

云端 Express 配对代登记（评估 2026-11-11 第 4.2 节，步 G）：桥本机存了云端连接（``cloud_link/link_store.py``）时 ``state()``
多一项 ``cloud_link: true``（没有云端连接的电脑 state() 逐字节不变、不导入 cloud_link）。对话页据此调 ``cloud_pending``：
回本站还没登记的云端电脑 ``[{host, device: {key, name}}]``（设备密钥是桥为云端那台生成的，设备名是云端主机名），对话页照
登记这台电脑的同一个接口登记，新发的令牌经 ``cloud_token {host, token}`` 交回桥只存本机（不配对、不进地址栏）。两个动作都只认
对话页所在、这台电脑连着的站点；令牌的通道要与本站成员相同。

「设置 → 连接」的「云端 Express」一行（步 E，用户 11-16；逻辑在 ``cloud_link/settings_link.py``，只在调到时导入）：
``cloud_prefill``（连接弹窗预填主机、端口、用户名，不含任何密码）、``cloud_connect {host, port, user}``（同 MR.ERP 授权
（ADR-026 第 7 条）：密码不经页面，桥弹 CC 原生小窗（``login_dialog.ask``，账号预填页面上那个）收账号和密码，只存本机凭据管理器；
存连接配置、生成云端设备密钥；存好后在界面线程起这条连接的宿主 ``cloud_link/host.py``——隧道盘、会话交接、中转、可见的连接
窗口（默认最小化），回包的状态换成会话实况；一个桥一个宿主，再点「连接」先停旧的再起）、``cloud_disconnect``（先停宿主——关窗即
断开、停中转、撤隧道盘——再 ``LinkStore.remove``）、``cloud_status``（连接与状态词，状态来自宿主的会话）。人把连接窗口关掉
= 断开，宿主随之停。**CC 启动时就算存有云端连接也不自动连**，等人在设置里点「连接」。
旧版桥没有这几个动作（回 ``{ok: false}``），页面据此不出这一行；``state()`` 与 FEATURES 不变。

云端代录的 Express 登录（做法页 11-16 第二版，``cloud_link/cred_handoff.py``）：``cloud_operator_authorize {connection}`` 弹 CC
原生小窗（``login_dialog.ask``，同本地代录的「授权登录」），用户本人输 Express 用户名和密码，只存桥本机凭据管理器
``Pearnly CC/cloud-express/<云端主机>/<连接>``（页面不收密码，回包不含密码）；云端那份 CC 要用时经隧道 ``ctl\\`` 加密交过去
（宿主 ``cloud_link/host.py`` 处理）。``cloud_operator_forget {connection}`` 删本机这条，并经 ``ctl`` 请云端删掉它那条（宿主没起
时先记着，下次起宿主补发）。前端按钮另做；旧版桥回 ``{ok: false}``。

窗口在自己的进程里（2026-10-18 轻量化，window_link.py / window_main.py）：这个桥在托盘进程里照旧应答，窗口进程的
``RemoteBridge`` 把页面的调用转过来；选择文件夹框经 ``pick`` 请窗口进程弹（以 CC 窗口为父），窗口按钮在窗口进程里做。

协议（网页侧见 frontend/assistant/src/lib/desktop.ts）：
- state() → 当前设置的 JSON；本机状态变了发 changed()，网页再取一次。
- call(method, args_json) → 结果 JSON；慢的操作（配对、换 Express 文件夹、找 Express）先回 {"job": id}，
  做完发 done(id, 结果 JSON)。
"""
from __future__ import annotations

import json
import uuid
from typing import Callable, Optional, Tuple
from urllib.parse import urlsplit

from PySide6.QtCore import QObject, QStandardPaths, Signal, Slot
from PySide6.QtWidgets import QFileDialog

from . import folder_plan as plan
from .desktop_settings import DesktopSettings, inside, picker_start, program_folder
from .gui_pairing_support import Probes
from .i18n import LANGS, normalize, t
from .logger import get_logger

log = get_logger(__name__)

REFUSED = json.dumps({"ok": False, "refused": True})
# 本版桥能做的事（页面按这里判断，旧版没有这一项）：company_folders = 公司的「选择文件夹…」（pick_folder）；
# devices = 多台电脑（ADR-021，1.7.0）：state() 带 ``device``（设备密钥与电脑名，对话页每次打开带它调 register）、成员带
# ``device_state``（这把密钥不再用、要重新登记），``remove`` 先吊销本机这把密钥再删成员（慢，回 job）；
# sites = 每个成员带自己的站点与通道（1.11.0，ADR-025）：同一台电脑可以同时连一个 Cowork 站点和一个 ERP 站点，ERP 站点的
# 对话页据此登记这台电脑（配对码 ``pcc_…``）。
# operator = 代录 Express（10-26，入口与直写分开）：operator_state / operator_control / operator_frame / operator_authorize。
# mrerp_login = MR.ERP 登录在 CC 里授权（ADR-026 第 7 条，11-02）：mrerp_authorize 收对话页发的一次性票，经这台电脑连着这个站点的
# 成员的机器通道交给站点，站点收下了再存本机凭据管理器，只回成没成。
# mrerp_login_inline = mrerp_authorize 另收页面「连接 MR.ERP」弹窗里输的 username / password（11-23），不再弹原生小窗；没有这项的
# 旧桌面版页面提示更新。
# place_channels = 每个位置给哪几种成员读（11-07 共用电脑隔离）：state() 带 ``place_channels``，``place_channels`` 动作改一个位置的勾。
FEATURES = ("company_folders", "devices", "sites", "express_places", "operator", "mrerp_login", "place_channels",
            "mrerp_login_inline")


def _local_login(parent, lang: str, title_key: str, user: str) -> Optional[Tuple[str, str]]:
    """在本进程弹账号密码小窗（以 ``parent`` 为父、置前）：单进程与测试用；托盘里桥收的是 ``WindowHost.login``。"""
    from .express_operator import login_dialog

    return login_dialog.ask(lang, parent, title_key=title_key, user=user)


class TomoBridge(QObject):
    changed = Signal()
    done = Signal(str, str)
    open_settings = Signal(str)   # 托盘「连接与设置」：让网页打开设置的某一栏

    def __init__(self, settings: DesktopSettings, *, allowed: Callable[[], bool], window: Callable[[], object],
                 set_lang: Callable[[str], None], page_url: Callable[[], str] = lambda: "",
                 pick: Optional[Callable[[str, str], str]] = None,
                 on_attention: Callable[[str, bool], None] = lambda _session, _visible: None,
                 cloud_links: Optional[Callable[[], object]] = None,
                 cloud_new: Optional[Callable[[], object]] = None,
                 session_rdp: Optional[Callable[[], object]] = None,
                 cloud_session: Optional[Callable[[], object]] = None,
                 cloud_host: Optional[Callable[[str, Callable], object]] = None,
                 ask_express_login: Optional[Callable[[str], object]] = None,
                 cloud_cred_store: Optional[Callable[[str, str, str], None]] = None,
                 cloud_cred_forget: Optional[Callable[[str], object]] = None,
                 ask_login: Optional[Callable[[str, str, str], Optional[Tuple[str, str]]]] = None) -> None:
        super().__init__()
        self._settings = settings
        self._allowed = allowed
        self._page_url = page_url   # 对话页当前地址：pick_folder 核对它的站点就是这台电脑连着的那家公司
        self._window = window
        self._set_lang = set_lang
        # pick(标题, 从哪打开) → 选好的文件夹（没选是空串）：默认在本进程弹（以窗口为父）；托盘里经窗口进程弹（WindowHost.pick）
        self._pick = pick or (lambda title, start: QFileDialog.getExistingDirectory(self._window(), title, start))
        # ask_login(界面语言, 标题词条, 预填用户名) → (用户名, 密码) 或 None：默认在本进程弹（以窗口为父、置前）；托盘里经窗口进程弹
        # （WindowHost.login）。要人输账号密码的原生小窗都走它
        self._ask_login = ask_login or (lambda lang, title_key, user: _local_login(self._window(), lang, title_key, user))
        self._probes = Probes()
        # 对话页报「现在开着哪段对话、看不看得见」（1.12.23 ⑨）：托盘据此判这条对话提醒要不要弹
        self._on_attention = on_attention
        # 本机的云端连接（cloud_link.link_store.LinkStore，没有云端连接回 None）；测试注入
        self._cloud_links = cloud_links or _cloud_store
        # 还没有云端连接时新建一份（不建文件，存的时候才写）、本机 TSplus 的 Session.rdp；测试注入
        self._cloud_new = cloud_new or _cloud_store_new
        self._session_rdp = session_rdp or _session_rdp
        # 云端连接宿主（cloud_link.host.CloudHost）：(主机, 状态回调) → 宿主；一个桥最多一个，点「连接」时才建；测试注入
        self._cloud_host_new = cloud_host or _cloud_host_new
        self._cloud_host = None
        # 连接窗口的会话：默认取宿主的（宿主没起是 None = 未连接）；测试注入
        self._cloud_session = cloud_session or self._host_session
        # 云端代录的 Express 登录：原生小窗（语言）→ (用户名, 密码) 或 None；桥本机凭据的存 / 删（目标名, …）；测试注入
        self._ask_express_login = ask_express_login or _ask_express_login
        self._cloud_cred_store = cloud_cred_store or _cloud_cred_store
        self._cloud_cred_forget = cloud_cred_forget or _cloud_cred_forget
        # 宿主没起时点了「取消授权」的连接号：下次起宿主时请云端删
        self._cloud_forgets: set = set()

    @Slot(result=str)
    def state(self) -> str:
        if not self._allowed():
            return REFUSED
        try:
            # window.frameless：本版窗口没有系统标题栏，对话页要自己画（旧版桌面端没有这一项，页面就不画，免得两层标题栏）
            extra = {"cloud_link": True} if self._has_cloud() else {}
            return json.dumps({**self._settings.snapshot(), "window": {"frameless": True}, "features": list(FEATURES),
                               **extra}, ensure_ascii=False)
        except Exception:
            log.exception("读取本机设置失败")
            return json.dumps({"ok": False})

    @Slot(str, str, result=str)
    def call(self, method: str, args_json: str) -> str:
        if not self._allowed():
            return REFUSED
        try:
            args = json.loads(args_json or "{}")
        except ValueError:
            args = {}
        lang = str(args.get("lang") or "")
        try:
            result = self._dispatch(str(method), args, lang)
        except Exception:
            log.exception("本机设置操作失败：%s", method)
            result = {"ok": False, "message": t("profiles_error", normalize(lang))}
        return json.dumps(result, ensure_ascii=False)

    def _dispatch(self, method: str, args: dict, lang: str) -> dict:
        s = self._settings
        member = str(args.get("id") or "")
        if method == "attention_seen":
            # 页面切换对话、可见性 / 焦点变化时各报一次（不轮询）：session 是当前对话编号（没开对话为空），visible 是看得见且有焦点
            self._on_attention(str(args.get("session") or "")[:200], args.get("visible") is True)
            return {"ok": True}
        if method == "lang":
            if lang in LANGS:
                self._set_lang(lang)
            return {"ok": True}
        if method == "pair":
            code, profile_id = str(args.get("code") or ""), (member or None)
            switch = bool(args.get("switch"))
            return self._job(lambda: s.pair(code, lang, profile_id, switch))
        if method == "detect":
            return self._job(lambda: {"ok": True, "roots": s.detect()})
        if method == "pick_roots":
            picked = self._pick(t("browse_title", normalize(lang)), self._start(s.snapshot().get("express_roots") or []))
            if not picked:
                return {"ok": False, "cancelled": True}
            return self._job(lambda: s.change_roots(picked, lang))
        if method == "rescan":
            return self._job(lambda: s.rescan(lang))
        if method == "pause":
            return self._changed(s.set_paused(member, bool(args.get("paused"))))
        if method == "add_place":
            # 「Express 位置」添加一个盘 / 文件夹（用户 10-24）：只认这里弹的原生选择框，args 里的路径不看
            places = s.snapshot().get("express_places") or s.snapshot().get("express_roots") or []
            picked = self._pick(t("browse_title", normalize(lang)), self._start(places))
            if not picked:
                return {"ok": False, "cancelled": True}
            return self._job(lambda: s.add_place(picked, lang))
        if method == "remove_place":
            path = str(args.get("path") or "")
            return self._job(lambda: s.remove_place(path, lang))
        if method == "place_channels":
            # 一个位置给哪几种成员读（11-07 共用电脑隔离）：只认位置清单里已有的位置
            path = str(args.get("path") or "")
            kinds = [str(item) for item in args.get("channels") or [] if isinstance(item, str)]
            return self._job(lambda: s.set_place_channels(path, kinds))
        if method == "remove":
            # 断开连接：先请服务端吊销本机这把密钥（device_leave，要连网），再删成员；做完由 _job 回界面线程重启各线程
            return self._job(lambda: s.disconnect(member))
        if method == "pick_folder":
            # 公司的「选择文件夹…」：args 里的路径一概不看，只认这里弹的原生选择框
            failed = s.pick_ready(self._page_host(), lang)
            if failed is not None:
                return failed
            current = [path for row in s.snapshot().get("members") or [] for path in row.get("folders") or []]
            picked = self._pick(t("folders_pick", normalize(lang)), self._start(current))
            if not picked:
                return {"ok": False, "cancelled": True}
            host = self._page_host()   # 选择框开着时页面可能换了：后台再核一次
            return self._job(lambda: s.pick_folder(picked, host, lang))
        if method == "add_folder":
            # 旧设置页（回退、旧服务端）的「添加文件夹…」：保留，判定口径同公司选文件夹（不许整块盘、系统位置、程序文件夹）
            current = self._folders(member)
            picked = self._pick(t("folders_pick", normalize(lang)), self._start(current))
            if not picked:
                return {"ok": False, "cancelled": True}
            program = program_folder()
            if program and (inside(picked, program) or inside(program, picked)):
                return {"ok": False, "message": t("folders_program", normalize(lang))}
            if plan.too_broad(picked):
                return {"ok": False, "message": t("folders_too_broad", normalize(lang))}
            if picked in current:
                return {"ok": True}
            return self._changed(s.set_folders(member, current + [picked], lang))
        if method == "remove_folder":
            path = str(args.get("path") or "")
            return self._changed(s.set_folders(member, [p for p in self._folders(member) if p != path], lang))
        if method == "autostart":
            return self._changed(s.set_autostart(bool(args.get("enabled"))))
        if method == "keep_awake":
            return self._changed(s.set_keep_awake(enabled=bool(args.get("enabled"))))
        if method == "keep_awake_on_battery":
            return self._changed(s.set_keep_awake(on_battery=bool(args.get("enabled"))))
        if method == "update":
            return self._changed(s.update())
        if method == "download_update":
            return s.download()
        if method == "logs":
            return s.open_logs()
        if method.startswith("operator_"):
            return self._operator(method, args, lang)
        if method == "mrerp_authorize":
            return self._mrerp_authorize(args, lang)
        if method == "cloud_pending":
            return self._cloud_pending()
        if method == "cloud_token":
            return self._cloud_token(args)
        if method in ("cloud_prefill", "cloud_connect", "cloud_disconnect", "cloud_status"):
            return self._cloud_settings(method, args, lang)
        if method in ("cloud_operator_authorize", "cloud_operator_forget"):
            return self._cloud_operator(method, args, lang)
        if method == "window":
            # 对话页画的标题栏（同 Claude 桌面端）：最小化 / 最大化 / 关闭 / 拖动 / 缩放（gui_tomo.TomoWindow.window_action）
            window = self._window()
            return window.window_action(str(args.get("action") or ""), args) if window is not None else {"ok": False}
        return {"ok": False}

    def _operator(self, method: str, args: dict, lang: str) -> dict:
        """代录 Express（入口与直写分开，评估 §10）：批次进度、暂停 / 继续 / 停止 / 跳过、实时画面、授权登录。代录包按需导入，
        托盘不代录时这几个调用只读一份空清单。授权登录弹的是原生小窗，页面拿不到密码，只回成没成。"""
        if str(args.get("target") or "") == "mrerp" and method in ("operator_authorize", "operator_forget"):
            return self._mrerp_operator_login(method, args, lang)
        from .express_operator import host as operator_host

        current = operator_host.host()
        if method == "operator_state":
            from .express_operator import login_dialog

            state = {"ok": True, "batches": current.snapshots(), "login": login_dialog.stored(),
                     # 已授权的 Express 账户名（直写与代录都记这个人为录入人；不回密码）
                     "login_user": login_dialog.stored_user()}
            login = self._mrerp_login_module()
            if login is not None:
                # 这台电脑存了哪些 MR.ERP 连接的登录（只有连接号，第 2 步 mrerp_operator.login.stored）
                state["mrerp_logins"] = list(login.stored())
            return state
        if method == "operator_control":
            results = args.get("results") if isinstance(args.get("results"), dict) else None  # settle：站点对各张单的结论
            ok = current.control(str(args.get("batch") or ""), str(args.get("action") or ""), str(args.get("doc") or ""),
                                 results)
            return {"ok": ok}
        if method == "operator_frame":
            return {"ok": True, "png": current.frame(str(args.get("batch") or ""))}
        if method == "operator_show":
            return {"ok": current.show(str(args.get("batch") or ""))}
        if method == "operator_authorize":
            from .express_operator import login_dialog

            return {"ok": login_dialog.ask_and_store(normalize(lang), asker=self._ask_login)}
        if method == "operator_forget":
            from .express_operator import login_dialog

            return {"ok": login_dialog.forget()}
        return {"ok": False}

    @staticmethod
    def _mrerp_login_module():
        """第 2 步代录 MR.ERP 的本机登录（``mrerp_operator.login``）；这一版没有这个包、或 MR.ERP 代录开关没开（默认）回 None，
        与没有执行层时一样诚实回不支持，不导入执行层。"""
        from .payload_contract import mrerp_operator_enabled

        if not mrerp_operator_enabled():
            return None
        try:
            from .mrerp_operator import login  # type: ignore[import-not-found]
        except ImportError:
            return None
        return login

    def _mrerp_operator_login(self, method: str, args: dict, lang: str) -> dict:
        """实操页「MR.ERP 登录」（第 2 步）：``{target: "mrerp", url, connection}`` 交给 ``mrerp_operator.login`` 的
        ``ask_and_store`` / ``forget``（只存本机，站点那份走 ``mrerp_authorize``）。这一版没有那个包：诚实回不支持。"""
        login = self._mrerp_login_module()
        if login is None:
            return {"ok": False, "code": "unsupported"}
        url, connection = str(args.get("url") or ""), str(args.get("connection") or "")
        if method == "operator_authorize":
            return {"ok": bool(login.ask_and_store(url, connection, normalize(lang), asker=self._ask_login))}
        return {"ok": bool(login.forget(url, connection))}

    def _mrerp_authorize(self, args: dict, lang: str) -> dict:
        """MR.ERP 登录在 CC 里授权（ADR-026 第 7 条）：收票与「连接 MR.ERP」弹窗里输的用户名、密码（11-23，地址一概不看），对话页
        必须是这台电脑连着的站点；在后台交给站点、再存本机（``mrerp_login.authorize``）。旧对话页只带票：经 ``ask_login`` 弹原生
        小窗收。用户名、密码只给了一样不收。回 ``{ok}`` 或 ``{ok: false, code | cancelled}``，从不回密码、不写日志。"""
        from . import mrerp_login

        ticket = str(args.get("ticket") or "")
        if not mrerp_login.valid_ticket(ticket):
            return {"ok": False, "code": "login_invalid"}
        username, password = args.get("username"), args.get("password")
        args = None   # 不再留着页面交来的密码
        typed = username is not None or password is not None
        if typed and not (isinstance(username, str) and username and isinstance(password, str) and password):
            return {"ok": False, "code": "login_invalid"}
        member = self._settings.member_for(self._page_host())
        if member is None:
            return {"ok": False, "code": "not_connected"}
        found = (username, password) if typed else self._ask_login(normalize(lang), "mrerp_login_title", "")
        username = password = None
        if found is None:
            return {"ok": False, "cancelled": True}
        site, backend_url, token = member
        username, password = found
        found = None
        return self._job(lambda: mrerp_login.authorize(site=site, backend_url=backend_url, token=token, ticket=ticket,
                                                       username=username, password=password))

    def _has_cloud(self) -> bool:
        try:
            store = self._cloud_links()
            return bool(store is not None and store.links)
        except Exception:
            log.exception("读本机的云端连接失败")
            return False

    def _cloud_pending(self) -> dict:
        """本站还没登记的云端电脑（设备密钥 + 设备名），只交给这台电脑连着的站点自己的对话页。"""
        member = self._settings.member_for(self._page_host())
        if member is None:
            return {"ok": False, "code": "not_connected"}
        store = self._cloud_links()
        if store is None:
            return {"ok": True, "links": []}
        return {"ok": True, "links": [{"host": link.host, "device": {"key": store.device_key(link.host),
                                                                     "name": link.device_name}}
                                      for link in store.pending(member[0])]}

    def _cloud_token(self, args: dict) -> dict:
        """对话页登记云端电脑后交回的令牌：通道与本站成员相同才存（link_store，只存本机）。从不回令牌。"""
        from . import channel

        member = self._settings.member_for(self._page_host())
        if member is None:
            return {"ok": False, "code": "not_connected"}
        site, backend_url, own_token = member
        token = str(args.get("token") or "")
        if not channel.known(token) or channel.of_token(token) != channel.of_token(own_token):
            return {"ok": False, "code": "token_invalid"}
        store = self._cloud_links()
        host = str(args.get("host") or "")
        if store is None or store.get(host) is None:
            return {"ok": False, "code": "not_found"}
        store.set_token(host, site, backend_url, token)
        return {"ok": True}

    def _cloud_operator(self, method: str, args: dict, lang: str) -> dict:
        """云端代录的 Express 登录：授权 = 原生小窗收用户名和密码、只存桥本机；取消授权 = 删本机这条并请云端删。
        args 里只看 ``connection``（用户名、密码一概不看）；回包从不含密码。"""
        from .cloud_link import cred_handoff

        try:
            connection = cred_handoff.connection_of(args.get("connection") or "default")
        except cred_handoff.Refused:
            return {"ok": False, "code": "invalid"}
        store = self._cloud_links()
        link = store.links[0] if store is not None and store.links else None   # 一个桥最多一条云端连接
        if link is None:
            return {"ok": False, "code": "not_found"}
        target = cred_handoff.bridge_target(link.host, connection)
        if method == "cloud_operator_authorize":
            found = self._ask_express_login(normalize(lang))
            if not found:
                return {"ok": False, "cancelled": True}
            user, password = found
            found = None
            try:
                self._cloud_cred_store(target, user, password)
            except Exception as exc:
                log.warning("存云端代录登录失败 · 连接=%s: %s", connection, type(exc).__name__)
                return {"ok": False, "code": "credential_failed"}
            finally:
                password = ""
            log.info("云端代录登录已授权 · 连接=%s", connection)
            return {"ok": True}
        try:
            removed = bool(self._cloud_cred_forget(target))
        except Exception as exc:
            log.warning("删云端代录登录失败 · 连接=%s: %s", connection, type(exc).__name__)
            removed = False
        host = self._cloud_host
        if host is not None and getattr(host, "host", "") == link.host and getattr(host, "running", False):
            try:
                host.request_forget(connection)
            except Exception as exc:
                log.warning("请云端删登录失败（下次起连接补发） · 连接=%s: %s", connection, type(exc).__name__)
                self._cloud_forgets.add(connection)
        else:
            self._cloud_forgets.add(connection)
        log.info("云端代录登录已取消授权 · 连接=%s", connection)
        return {"ok": True, "removed": removed}

    def _cloud_settings(self, method: str, args: dict, lang: str) -> dict:
        """「云端 Express」一行的四个动作。``cloud_connect`` 只收地址与预填的账号（args 里的密码一概不看）：地址不合规直接回，
        合规才在界面线程弹原生小窗收账号和密码，再在后台存本机凭据与连接配置（回 job）；删连接要连网吊销令牌，也在后台。"""
        from .cloud_link import settings_link

        if method == "cloud_prefill":
            return settings_link.prefill(self._cloud_links(), self._session_rdp())
        if method == "cloud_status":
            return settings_link.status(self._cloud_links(), self._cloud_session())
        if method == "cloud_disconnect":
            self._stop_cloud_host()   # 界面线程：关窗即断开、停中转、撤隧道盘
            store, session = self._cloud_links(), self._cloud_session()
            return self._job(lambda: settings_link.disconnect(store, session))
        if method == "cloud_connect":
            return self._cloud_connect(settings_link, args, lang)
        return {"ok": False}

    def _cloud_connect(self, settings_link, args: dict, lang: str) -> dict:
        host, port = str(args.get("host") or ""), args.get("port")
        failed = settings_link.check_address(host, port)
        if failed is not None:
            return failed
        found = self._ask_login(normalize(lang), "cloud_login_title", str(args.get("user") or ""))
        if not found:
            return {"ok": False, "cancelled": True}
        user, password = found
        found = None
        store, session_rdp, via = self._cloud_links(), self._session_rdp(), self._device_name()

        def work() -> dict:
            nonlocal password
            try:
                return settings_link.connect(store, new_store=self._cloud_new, session_rdp=session_rdp, host=host,
                                             port=port, user=user, password=password, via=via)
            finally:
                password = ""

        return self._job(work, after=lambda result: self._after_cloud_connect(settings_link, result))

    def _after_cloud_connect(self, settings_link, result: dict) -> dict:
        """（界面线程）存好了：起这条连接的宿主（先停旧的），回包的状态换成会话实况；起不来照样存着、回「未连接」。"""
        if not result.get("ok") or not result.get("host"):
            return result
        from .cloud_link.host import HostUnavailable

        self._stop_cloud_host()
        try:
            host = self._cloud_host_new(result["host"], self._cloud_changed)
            self._cloud_host = host
            host.start()
            for connection in sorted(self._cloud_forgets):
                host.request_forget(connection)
            self._cloud_forgets.clear()
        except HostUnavailable:
            log.info("这台电脑起不了云端连接窗口（不是 Windows 或没有 QtAxContainer）")
        except Exception as exc:
            log.warning("起云端连接失败：%s", type(exc).__name__)
        session = self._cloud_session()
        return {**result, "state": settings_link.status_of(session.snapshot() if session is not None else None)}

    def _host_session(self):
        host = self._cloud_host
        return host.session if host is not None else None

    def _cloud_changed(self, snapshot=None, description: str = "") -> None:
        """（界面线程）连接窗口状态变了：对话页重取状态。"""
        self.changed.emit()

    def _stop_cloud_host(self) -> None:
        host, self._cloud_host = self._cloud_host, None
        if host is not None:
            try:
                host.stop()
            except Exception:
                log.exception("停云端连接失败")

    def _device_name(self) -> str:
        try:
            device = self._settings.snapshot().get("device") or {}
            return str(device.get("name") or "") if isinstance(device, dict) else ""
        except Exception:
            return ""

    @staticmethod
    def _start(current: list) -> str:
        return picker_start(current, QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation),
                            program_folder())

    def _page_host(self) -> str:
        try:
            return (urlsplit(str(self._page_url() or "")).hostname or "").lower()
        except ValueError:
            return ""

    def _folders(self, member: str) -> list:
        row = next((m for m in self._settings.snapshot()["members"] if m["id"] == member), None)
        return list((row or {}).get("folders") or [])

    def _changed(self, result: dict) -> dict:
        self.changed.emit()
        return result

    def _job(self, work, after: Optional[Callable[[dict], dict]] = None) -> dict:
        job = uuid.uuid4().hex

        def finished(result) -> None:   # 回到界面线程
            result = dict(result or {"ok": False})
            if after is not None:
                try:
                    result = after(result)
                except Exception:
                    log.exception("本机设置后台任务的收尾失败")
            if result.pop("profiles_changed", False):
                try:
                    self._settings.profiles_changed()
                except Exception:
                    log.exception("重启取任务线程失败")
            self.done.emit(job, json.dumps(result, ensure_ascii=False))
            self.changed.emit()

        def failed(exc) -> None:
            log.error("本机设置后台任务失败：%r", exc)
            self.done.emit(job, json.dumps({"ok": False}))

        self._probes.spawn(job, work, finished, failed)
        return {"job": job}

    def shutdown(self) -> None:
        self._stop_cloud_host()
        self._probes.shutdown()


def _cloud_store():
    """本机的云端连接；没有那个文件（没连云端的电脑）回 None，不导入 cloud_link。"""
    from .config import CLOUD_LINK_FILE

    if not CLOUD_LINK_FILE.exists():
        return None
    from .cloud_link.link_store import LinkStore

    return LinkStore.load()


def _cloud_store_new():
    """还没有云端连接时新建一份空的（不建文件，``save_link`` 时才写）。"""
    from .cloud_link.link_store import LinkStore

    return LinkStore.load()


def _cloud_host_new(host: str, on_change):
    """这条云端连接的宿主（只在点「连接」时导入 cloud_link.host）；连接配置每次现读。"""
    from .cloud_link.host import CloudHost
    from .cloud_link.link_store import LinkStore

    return CloudHost(host, links=LinkStore.load, on_change=on_change)


def _ask_express_login(lang: str):
    """云端代录的 Express 用户名和密码：CC 原生小窗（同本地代录「授权登录」，``login_dialog.ask``），页面拿不到密码。"""
    from .express_operator import login_dialog

    return login_dialog.ask(lang)


def _cloud_cred_store(target: str, user: str, password: str) -> None:
    """存进桥本机凭据管理器（Generic，DPAPI）；不是 Windows 抛 RuntimeError。"""
    import sys

    from .express_operator import credentials

    if sys.platform != "win32":
        raise RuntimeError("credential store needs Windows")
    credentials.store(target, user, password)


def _cloud_cred_forget(target: str) -> bool:
    from .express_operator import credentials

    return credentials.forget(target)


def _session_rdp():
    """本机 TSplus 便携客户端的 ``Session.rdp``（``%USERPROFILE%\\RDP6``）；没有回 None。"""
    from pathlib import Path

    from .cloud_link.rdp import find_session_rdp

    return find_session_rdp(Path.home())
