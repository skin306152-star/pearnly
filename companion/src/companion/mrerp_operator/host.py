# -*- coding: utf-8 -*-
"""MR.ERP 目标的装配（ADR-026 第 4 条）：代录宿主（``express_operator.host``）按载荷 ``operator.target = "mrerp"`` 调这里的
``assemble``，拿回同一套 ``batch.Runner`` 和窗口要用的几样（实时画面、有人在用、存证）。

- 批次、进度落盘、暂停 / 停止 / 跳过、断点续录、存证、汇报都用 Express 那一套（``Runner``、``BatchStore``、``Evidence``）；
  注入 MR.ERP 的会话、引擎、账本，以及三处 MR.ERP 的口径：草稿形状（``draft_type``）、开录前清单（``before``）、续录前查存没存
  （``find_saved``）；
- 不拿账套写者锁（``no_lock``：MR.ERP 是多用户服务器程序，要互斥的是「同一个账号」，由站点的账号租约管，第 3 步）；
- 网络断：每分钟只读探一次登录页（``session.reachable``），10 分钟不恢复整批暂停（同 Express 的 -70 口径）；
- 每种单第一次先探路（开表单读格子、不存），按「网址 + 账套 + 配方版本 + 单种」记在本机，下次不再探。

两个开发开关（默认都关，与 main 行为一致）：
- ``PEARNLY_CC_MRERP_OPERATOR=1``：这台 CC 心跳报 ``mrerp`` 能力、宿主装配 MR.ERP（关着时站点不会派 MR.ERP 代录单，就算派来也
  回「不支持」，不碰任何网页）；
- ``PEARNLY_CC_MRERP_UNVERIFIED=1``：配方里没在真站点核对过的单种也录（只给假站点回放与第 0 步探路用）。
"""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, Optional

ENV_ENABLED = "PEARNLY_CC_MRERP_OPERATOR"
ENV_UNVERIFIED = "PEARNLY_CC_MRERP_UNVERIFIED"
PROBES = "mrerp-probe.json"
UNSURE = "mrerp-unsure.json"  # 点了存档、结果不明就停下的单（续录前先查，见 book.UnsureStore）


def enabled() -> bool:
    """MR.ERP 代录开没开（默认关：第 0 步真站点探路通过前不对外报这个能力）。与心跳同一个开关。"""
    from ..payload_contract import mrerp_operator_enabled

    return mrerp_operator_enabled()


def unverified_allowed() -> bool:
    return os.environ.get(ENV_UNVERIFIED, "").strip() == "1"


def settings_of(payload: Dict) -> SimpleNamespace:
    """宿主开批次用的「设置」：MR.ERP 没有本机账套文件夹，公司 / 账套 / 连接都从载荷来（不带密码）。"""
    from .draft import connection_of

    conn = connection_of(payload)
    return SimpleNamespace(account_dir=conn["url"], account_company=conn["company_name"], account_set=conn["name"],
                           mrerp=conn)


def draft_of(doc_id: str, payload: Dict):
    from .draft import from_payload

    return from_payload(doc_id, payload)


class DeskView:
    """CC 窗口要的两样：实时画面（只画这个 MR.ERP 窗口）、「查看」把窗口弹出来。"""

    def __init__(self, session):
        self.session = session

    WAIT_S = 0.5  # CC 窗口的线程来要：批次正在等页面时只等这么久，等不到就这次不给（不卡窗口）

    def screenshot(self, size) -> Dict:
        page = self.session.page
        try:
            return {"png": page.screenshot(tuple(size), wait=self.WAIT_S) if page is not None else ""}
        except Exception:  # noqa: BLE001 — 窗口已关
            return {"png": ""}

    def show_main(self) -> bool:
        page = self.session.page
        try:
            return bool(page is not None and page.show(wait=self.WAIT_S))
        except Exception:  # noqa: BLE001
            return False


def assemble(state, store, settings, root: Path, notify, *, member=None, desk_factory=None, read_secret=None, load_s=None,
             presence_options: Optional[Dict] = None, runner_options: Optional[Dict] = None):
    """MR.ERP 的装配：子进程网页窗口 + 本机凭据 + 本机截图。关键字参数只给测试换（假站点、快的等待）。
    ``member``（成员配置）与 Express 装配同一个调用形状收下；MR.ERP 还没接 AI 接手（Express 的 ``operator_ai``），认不出的页面照旧交人。"""
    from ..express_operator.batch import Runner, no_lock
    from ..express_operator.engine import NeedHuman
    from ..express_operator.evidence import Evidence, prune
    from ..express_operator.probe import ProbeStore
    from . import recipe as R
    from .book import MrerpBook, UnsureStore
    from .draft import Draft, connection_of
    from .engine import Engine
    from .presence import WebPresence
    from .session import Kicked, MrerpSession, NetworkDown
    from .web_desk import DeskGone, PageError

    conn = dict(getattr(settings, "mrerp", None) or connection_of({}))
    recipe = R.load()
    allow = unverified_allowed()
    holder: Dict = {}
    presence = WebPresence(lambda: holder["session"].human(),
                           stop_requested=lambda: holder["runner"].stop_requested(),
                           on_pause=lambda r: holder["runner"].presence_paused(r),
                           on_resume=lambda: holder["runner"].presence_resumed(), **(presence_options or {}))
    extra = {k: v for k, v in (("desk_factory", desk_factory), ("read_secret", read_secret), ("load_s", load_s)) if v}
    session = MrerpSession(conn, recipe, presence=presence, **extra)
    holder["session"] = session
    supported = lambda doc_type: recipe.supported(doc_type, allow_unverified=allow)  # noqa: E731
    book = MrerpBook(session, recipe, supported=supported, unsure=UnsureStore(Path(root) / UNSURE))
    evidence_root = Path(root) / "evidence"
    evidence = Evidence(evidence_root, state.id)
    engine = Engine(session, recipe, book, presence=presence, evidence=evidence.save, allow_unverified=allow)
    probes = ProbeStore(Path(root) / PROBES)
    account_key = f"{conn.get('url', '').lower()}|{conn.get('comidyear', '')}"

    def probe(doc_type: str):
        if not supported(doc_type):
            return None  # 不支持的单由引擎回「不支持」，不开页面
        cached = probes.get(account_key, recipe.version, doc_type)
        if cached is not None:
            return cached
        sample = next((Draft(**d.draft) for d in state.docs if d.doc_type == doc_type), None)
        if sample is None:
            return None
        try:
            result = engine.probe(sample)
        except Kicked as error:
            raise NeedHuman("account_in_use", f"kicked: {error}"[:300]) from None
        except (NetworkDown, DeskGone) as error:
            raise NeedHuman("network_down", str(error)[:300]) from None
        except PageError as error:
            return {"ok": False, "missing": [str(error)[:80]]}  # 页面对不上：这种单暂停，不记（下次再探）
        probes.put(account_key, recipe.version, doc_type, result)
        return result

    runner = Runner(state, store, engine, session, book, probe=probe, lock=no_lock, reachable=session.reachable,
                    on_change=notify, housekeeping=lambda: prune(evidence_root), evidence_state=evidence.state,
                    pauses=lambda: presence.evidence, draft_type=Draft, before=book.before, find_saved=book.find_saved,
                    **(runner_options or {}))
    holder["runner"] = runner
    return runner, {"desk": DeskView(session), "presence": presence, "evidence": evidence}
