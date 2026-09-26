# -*- coding: utf-8 -*-
"""Express 配对窗：探测真实账套，锁定客户选择，提供中泰双语设置。"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QMessageBox,
)

from . import pairing
from .account_probe import (
    AccountSet,
    probe_account_sets,
    probe_default_accounts,
)
from .express_locate import candidate_roots, sccomp_company_count
from .gui_pairing_support import (
    ProbeThread as _Probe,
    account_choice_label,
    build_pairing_ui,
    fit_to_content,
    pick_default as _pick_default,
    profile_root_candidates as _profile_root_candidates,
    rank_roots as _rank_roots,
    set_pairing_busy,
    same_root as _same_root,
    shutdown_probes,
    year_key,
)
from .i18n import (
    MessageSpec,
    StatusSpec,
    default_lang,
    normalize,
    persist_ui_lang,
    t,
)
from .logger import get_logger
from .profile_store import ProfileStore, ProfileStoreError

log = get_logger(__name__)
_year_key = year_key  # public compatibility for the root-selection tests


class PairingDialog(QDialog):
    def __init__(
        self,
        parent=None,
        lang: Optional[str] = None,
        profile_id: Optional[str] = None,
        profile_store: Optional[ProfileStore] = None,
    ) -> None:
        super().__init__(parent)
        self.result_info = None
        self._express_root: Optional[str] = None
        self._sets: List[AccountSet] = []
        self._roots: List = (
            []
        )  # [(root, 公司数)] · 探测到的各年度数据根(驱动「年度/数据根」下拉)
        self._alive = True  # 窗口存活标志:关窗后后台探测回调直接丢弃,不碰已销毁控件
        self._probes: List = []  # 在跑的后台探测(持引用防 GC)
        self._gen: dict = {}  # 各探测(kind→代次)· _spawn 首用自建,丢弃过期结果
        self._store = profile_store or ProfileStore.load()
        self._profile_id = profile_id
        if profile_id is None:
            self._cfg = self._store.new_runtime_config()
        else:
            self._cfg = next(
                (
                    cfg
                    for cfg in self._store.runtime_configs(include_paused=True)
                    if cfg.profile_id == profile_id
                ),
                None,
            )
            if self._cfg is None:
                raise ProfileStoreError("profile not found")
        # 已配过 → 预填已有设置(重新配对/打开设置时不必从零重输:账套/账号自动带出,
        # 科目映射自动沿用已配存值,配对码与密码留空即沿用当前)。首次配对(未 paired)仍空白。
        self._prefill = bool(self._cfg.paired)
        configured_roots = list(getattr(self._cfg, "authorized_roots", None) or ())
        self._share_all_roots = not self._prefill or len(configured_roots) > 1
        self.lang = normalize(lang or (self._cfg.ui_lang or default_lang()))
        self._status_spec = StatusSpec(MessageSpec("status_scanning"), True)
        self._root_placeholder_key = "ph_root"
        self._account_placeholder_key = "ph_acct_loading"
        self._pairing_in_progress = False
        from .gui_tray import pearnly_icon

        self.setWindowIcon(pearnly_icon())
        # 可最小化 + 不强制置顶 + 可缩放。
        self.setWindowFlags(
            Qt.Window | Qt.WindowMinimizeButtonHint | Qt.WindowCloseButtonHint
        )
        self.setMinimumWidth(540)
        self._build()
        # 高度随内容自适应、封顶屏幕(超出才显示滚动条)。
        try:
            scr = QApplication.primaryScreen().availableGeometry()
            self.setMaximumHeight(int(scr.height() * 0.94))
        except Exception:
            pass
        self._autodetect()
        self._fit_to_content()

    def _t(self, key: str, **fmt) -> str:
        return t(key, self.lang, **fmt)

    def _fit_to_content(self) -> None:
        fit_to_content(self)

    def showEvent(self, e) -> None:  # noqa: N802 (Qt 命名)
        super().showEvent(e)
        if not getattr(self, "_shown_once", False):
            self._shown_once = True
            self._fit_to_content()  # 显示后几何已稳定,再贴合一次确保收口准确

    def _build(self) -> None:
        build_pairing_ui(self)

    def _retranslate(self) -> None:
        """切语言后刷新所有可见文案(下拉项保留·只换标签)。"""
        self.setWindowTitle(
            self._t("win_title_edit" if self._profile_id else "win_title_add")
        )
        self.lang_btn.setText(self._t("lang_toggle"))
        self.intro.setText(self._t("intro"))
        self.sec_connect.setText(self._t("sec_connect"))
        self.lbl_code.setText(self._t("lbl_code"))
        self.token.setPlaceholderText(
            self._t("ph_code_keep" if self._prefill else "ph_code")
        )
        self.sec_account.setText(self._t("sec_account"))
        self.lbl_root.setText(self._t("lbl_root"))
        self.root_combo.setPlaceholderText(self._t(self._root_placeholder_key))
        for i, (r, n) in enumerate(self._roots):  # 年度根下拉项含单位词 → 随语言刷新
            self.root_combo.setItemText(i, self._root_item_label(r, n))
        self.lbl_acct.setText(self._t("lbl_acct"))
        self.help_acct.setText(self._t("help_acct"))
        self.all_roots.setText(self._t("chk_all_roots"))
        self.autostart.setText(self._t("chk_autostart"))
        self.browse_btn.setText(self._t("btn_browse"))
        self.cancel_btn.setText(self._t("btn_cancel"))
        self.pair_btn.setText(self._t("btn_pair"))
        self.account.setPlaceholderText(self._t(self._account_placeholder_key))
        self._render_status()

    def _toggle_lang(self) -> None:
        selected = "zh" if self.lang == "th" else "th"
        try:
            self.lang = persist_ui_lang(self._store, selected)
        except Exception:
            log.exception("保存界面语言失败")
            self._set_status("profiles_error", ok=False)
            return
        self._cfg.update(ui_lang=self.lang)
        self._retranslate()

    def _set_status(self, key: str, ok: bool = True, **fmt) -> None:
        self._status_spec = StatusSpec(MessageSpec(key, fmt), ok)
        self._render_status()

    def _set_pair_error(self, error_key: str, error_fmt=None) -> None:
        reason = MessageSpec(error_key or "pair_err_unexpected", error_fmt or {})
        self._status_spec = StatusSpec(MessageSpec("pair_fail", {"err": reason}), False)
        self._render_status()

    def _render_status(self) -> None:
        self.status.setStyleSheet(
            "color:#666" if self._status_spec.ok else "color:#c0392b"
        )
        self.status.setText(self._status_spec.message.render(self.lang))

    def _set_root_placeholder(self, key: str) -> None:
        self._root_placeholder_key = key
        self.root_combo.setPlaceholderText(self._t(key))

    def _set_account_placeholder(self, key: str) -> None:
        self._account_placeholder_key = key
        self.account.setPlaceholderText(self._t(key))

    def _spawn(self, kind: str, fn, on_ok, on_err=None):
        """后台跑 fn(网络探测),结果回主线程 on_ok/on_err。同类新探测使旧的作废(代次守卫)。"""
        self._gen[kind] = self._gen.get(kind, 0) + 1
        gen = self._gen[kind]
        w = _Probe(fn)
        self._probes.append(w)

        def _guard(cb):
            def _inner(res=None):
                # 只投递最新一代且窗口尚在的结果;过期/关窗回调直接吞掉(不碰已销毁控件)。
                if self._alive and gen == self._gen.get(kind) and cb is not None:
                    cb(res)

            return _inner

        # 线程真正结束(run 返回)后才松引用——否则丢掉运行中 QThread 的最后引用会崩。
        def _cleanup():
            if w in self._probes:
                self._probes.remove(w)

        w.ok.connect(_guard(on_ok))
        w.err.connect(_guard(on_err))
        w.finished.connect(_cleanup)
        w.start()

    def closeEvent(self, e):  # noqa: N802 (Qt 命名)
        if self._pairing_in_progress:
            e.ignore()
            return
        shutdown_probes(self)
        super().closeEvent(e)

    def reject(self) -> None:
        if self._pairing_in_progress:
            return
        shutdown_probes(self)
        super().reject()

    def _set_pairing_busy(self, busy: bool) -> None:
        set_pairing_busy(self, busy)

    def _autodetect(self) -> None:
        self.setWindowTitle(
            self._t("win_title_edit" if self._profile_id else "win_title_add")
        )
        # 换根按钮常驻:任意事务所 / 任意年度 / 探测选错年度根,都能手动改指(不必逼到探测失败)。
        self.browse_btn.setVisible(True)
        saved = (self._cfg.express_root or "").strip()
        # 探测数据根要读 \\accserver 上一堆 SCCOMP,慢 → 放后台,窗口先秒开、显示"正在检测"。
        self.lbl_root.setVisible(True)
        self.root_combo.setVisible(True)
        self._set_root_placeholder("ph_root_scanning")
        self._set_status("status_scanning", ok=True)

        def work():
            cands: List[str] = []
            if saved:  # 上次选定根优先复用(粘住),不因每次开窗重新挑而漂移。
                cands.append(saved)
            cands.extend(_profile_root_candidates(self._store, self._profile_id or ""))
            cands.extend(candidate_roots())
            return _rank_roots(cands)

        def done(roots):
            if not roots:
                self._set_root_placeholder("ph_root")
                self._sync_root_row([])
                self._set_status("browse_hint", ok=False)
                return
            prefer = (
                saved
                if any(_same_root(saved, r) for r, _ in roots)
                else _pick_default(roots)
            )
            self._sync_root_row(roots, prefer)

        def fail(e):
            log.error("自动探测 Express 数据根失败: %r", e)
            self._set_root_placeholder("ph_root")
            self._set_status("browse_hint", ok=False)

        self._spawn("detect", work, done, fail)

    def _root_item_label(self, root: str, n: int) -> str:
        return self._t("root_item", name=Path(root).name or str(root), n=n)

    def _sync_root_row(self, roots, prefer: Optional[str] = None) -> None:
        """填「年度/数据根」下拉:多套时显示并选 prefer(触发装载账套),≤1 套时隐藏该行仍装载唯一根。"""
        self._roots = list(roots)
        self.root_combo.blockSignals(True)
        self.root_combo.clear()
        for r, n in roots:
            self.root_combo.addItem(self._root_item_label(r, n), r)
        self.root_combo.blockSignals(False)
        multi = len(roots) > 1
        self.lbl_root.setVisible(multi)
        self.root_combo.setVisible(multi)
        if not roots:
            return
        idx = 0
        if prefer:
            for i, (r, _n) in enumerate(roots):
                if _same_root(r, prefer):
                    idx = i
                    break
        self.root_combo.setCurrentIndex(idx)  # -1→idx 必触发 _on_root_changed → 装载

    def _on_root_changed(self, _idx: int = -1) -> None:
        root = self.root_combo.currentData()
        if root:
            self._load_root(str(root))

    def _browse_root(self) -> None:
        p = QFileDialog.getExistingDirectory(self, self._t("browse_title"))
        if not p:
            return
        for i in range(self.root_combo.count()):  # 已在列表 → 直接选中(无需再数)
            if _same_root(self.root_combo.itemData(i), p):
                self.root_combo.setCurrentIndex(i)
                self.lbl_root.setVisible(self.root_combo.count() > 1)
                self.root_combo.setVisible(self.root_combo.count() > 1)
                return
        # 新根:数登记公司(读 \\accserver 上 SCCOMP)也走后台,不冻结 UI;只数一次,复用于校验+标签。
        self._set_status("status_scanning", ok=True)

        def done(n):
            if n <= 0:  # 不是有效 Express 数据根(无 secure/SCCOMP)
                self._set_status("status_reg_error", ok=False)
                return
            self.root_combo.blockSignals(True)  # 新根插到顶部
            self.root_combo.insertItem(0, self._root_item_label(p, n), p)
            self.root_combo.blockSignals(False)
            self._roots.insert(0, (p, n))
            self.lbl_root.setVisible(True)
            self.root_combo.setVisible(True)
            self.root_combo.setCurrentIndex(0)  # 触发装载

        def fail(e):
            log.error("读取所选 Express 数据根失败: %r", e)
            self._set_status("status_reg_error", ok=False)

        self._spawn("detect", lambda: sccomp_company_count(p), done, fail)

    def _load_root(self, root: str) -> None:
        # 读该根的账套名单(SCCOMP + 每家 ISINFO)也走网络 → 后台,期间账套下拉显示"加载中"。
        self.account.clear()
        self._set_account_placeholder("ph_acct_loading")
        self._set_status("ph_acct_loading", ok=True)

        def done(sets):
            if not sets:
                self._set_status("status_reg_error", ok=False)
                self.browse_btn.setVisible(True)
                return
            self._express_root = root
            self._sets = sets
            self.account.clear()
            for acc in sets:
                self.account.addItem(account_choice_label(acc), acc)
            # 预填:命中已配账套则选中(触发默认科目向量探测,供全自动映射);未命中或首次配对留未选。
            self.account.setCurrentIndex(self._prefill_account_index(sets))
            self._set_account_placeholder("ph_acct_select")
            self._set_status("status_found", n=len(sets), ok=True)
            self.browse_btn.setVisible(True)  # 不是要的年度/事务所时可换根

        def fail(e):
            log.error("读取 Express 账套列表失败: %r", e)
            self._set_status("status_reg_error", ok=False)
            self.browse_btn.setVisible(True)

        self._spawn("root", lambda: probe_account_sets(root), done, fail)

    def _prefill_account_index(self, sets) -> int:
        """已配过 → 按账套 PATH(唯一键)命中已选账套的下标;未命中/首次返 -1(未选)。"""
        if not self._prefill:
            return -1
        want = (self._cfg.account_set or self._cfg.account_dir or "").strip()
        if not want:
            return -1
        for i, acc in enumerate(sets):
            if str(getattr(acc, "path", "") or "").strip() == want:
                return i
        return -1

    def _on_account_changed(self, _idx: int = -1) -> None:
        acc = self.account.currentData()
        if acc is not None:
            self._load_defaults(acc)

    def _load_defaults(self, acc) -> None:
        """读所选账套 ISINFO 默认科目向量 → 存 self._defaults,供配对时全自动映射。

        不再填任何下拉(科目映射界面已删,机制保留):ISINFO 在 \\accserver 上 → 后台读,不冻结 UI。
        """
        path = str(getattr(acc, "path", "") or "")

        def work():
            try:
                return probe_default_accounts(path) if path else {}
            except Exception:
                return {}

        def done(defaults):
            self._defaults = defaults

        self._spawn("defaults", work, done)

    def _auto_mapping(self, chosen) -> dict:
        """科目映射全自动:同一账套重配沿用存值(保住人工改过的码),换账套用其 ISINFO 默认。

        存值只在「本机上次配对的就是这个账套」时可信(判据同 _prefill_account_index:
        比 account_set PATH 唯一键)——旧下拉靠科目表 findData 隐式挡掉跨账套串码,
        界面删了,这道闸落到这里。
        """
        defaults = getattr(self, "_defaults", {}) or {}
        want = (self._cfg.account_set or self._cfg.account_dir or "").strip()
        same_set = bool(
            self._prefill
            and want
            and str(getattr(chosen, "path", "") or "").strip() == want
        )
        out = {}
        for key in pairing.ACCOUNT_MAP_KEYS:
            stored = (getattr(self._cfg, key, "") or "").strip() if same_set else ""
            out[key] = stored or str(defaults.get(key, "") or "")
        return out

    def _on_pair(self) -> None:
        chosen = self.account.currentData()
        if chosen is None:
            self._set_status("pick_account_first", ok=False)
            return
        mapping = self._auto_mapping(chosen)
        self._set_pairing_busy(True)
        self._set_status("pairing", ok=True)
        token, root, sets = self.token.text(), self._express_root or "", self._sets
        autostart, lang = self.autostart.isChecked(), self.lang
        share_all_roots = bool(
            getattr(self, "all_roots", None) and self.all_roots.isChecked()
        )
        authorized_roots = (
            [candidate for candidate, _count in self._roots]
            if share_all_roots
            else [root]
        )

        def work():
            return pairing.pair(
                token=token,
                chosen=chosen,
                all_sets=sets,
                express_root=root,
                enable_autostart=autostart,
                mapping=mapping,
                authorized_roots=authorized_roots,
                lang=lang,
                config=self._cfg,
                profile_store=self._store,
            )

        def done(res):
            self._set_pairing_busy(False)
            if res.ok:
                self.result_info = res
                QMessageBox.information(
                    self,
                    self._t("pair_ok_title"),
                    self._t(
                        "pair_ok_body",
                        company=res.company,
                        n=res.account_sets,
                    ),
                )
                self.accept()
            else:
                self._set_pair_error(res.error_key, res.error_fmt)

        def fail(e):
            log.error("配对后台任务异常: %r", e)
            self._set_pairing_busy(False)
            self._set_pair_error("pair_err_unexpected")

        try:
            self._spawn("pair", work, done, fail)
        except Exception:
            log.exception("启动配对后台任务失败")
            self._set_pairing_busy(False)
            self._set_pair_error("pair_err_unexpected")


_OPEN_DIALOG: Optional[PairingDialog] = None


def open_pairing(
    parent=None,
    *,
    lang: Optional[str] = None,
    profile_id: Optional[str] = None,
    profile_store: Optional[ProfileStore] = None,
) -> bool:
    """打开进程内唯一配对窗并返回是否配对成功。"""
    global _OPEN_DIALOG
    if _OPEN_DIALOG is not None:
        try:
            if _OPEN_DIALOG.isVisible():
                _OPEN_DIALOG.raise_()
                _OPEN_DIALOG.activateWindow()
                return False
        except RuntimeError:
            pass  # 旧 Qt 对象已被回收 → 当作没开着
        _OPEN_DIALOG = None
    if QApplication.instance() is None:
        QApplication([])
    _OPEN_DIALOG = PairingDialog(
        parent, lang=lang, profile_id=profile_id, profile_store=profile_store
    )
    try:
        return _OPEN_DIALOG.exec() == QDialog.Accepted
    finally:
        _OPEN_DIALOG = None


def run_pairing() -> bool:
    """首次未配对时 main 调:跑配对窗,返回是否成功。复用 open_pairing 的单例守卫。"""
    return open_pairing()
