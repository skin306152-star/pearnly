# -*- coding: utf-8 -*-
"""首次配对:校验配对码 → 锁定客户所选账套 → 存 config(method=dbf)→ 写自启。

配对码 = 网页连接弹窗复制出来的 `exp_<endpoint_id>_<secret>@<租户域名>`:@ 前是密钥,
@ 后是这家租户的站点(小助手从此连它,不再写死某个域名)。客户在配对窗从**探测到的真实账套
列表**里选自家那一个(显示真公司名),配对据此一步到位:确认码有效(连云端 heartbeat)、
把所选账套的目录/公司/账套名/grid 行号锁进 config(= 写盘唯一合法目标)、把探测列表 +
所选账套上报云端(供 FE 选/镜像)、打开开机自启。纯逻辑(无 GUI),供配对窗 + 单测调用。
"""

from __future__ import annotations

import ntpath
import re
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import List, Optional

import httpx

from . import autostart
from .account_probe import AccountSet, probe_account_sets
from .config import CompanionConfig
from .i18n import http_status_category, t
from .logger import get_logger
from .profile_store import ProfileStore, ProfileStoreError
from .queue_client import QueueClient, QueueClientConfig

log = get_logger(__name__)

# 配对码 @ 后面的站点:域名(可带端口)。本机开发用的 localhost / *.localhost / 127.0.0.1 走 http,其余一律 https。
_HOST = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?(?::\d{1,5})?$")
_LOCAL_HOSTS = ("localhost", "127.0.0.1")


def split_pairing_code(code: str) -> tuple[str, str]:
    """`exp_…@租户域名` → (密钥, 站点 URL);格式不对返回 ("", "")。"""
    value = "".join(str(code or "").split())
    token, sep, host = value.rpartition("@")
    name = host.split(":")[0].lower()
    local = name in _LOCAL_HOSTS or name.endswith(".localhost")
    if not sep or not token.startswith("exp_") or not _HOST.match(host) or ("." not in name and not local):
        return "", ""
    scheme = "http" if local else "https"
    return token, f"{scheme}://{host.lower()}"

# 科目映射 6 键 · 载荷/配置/配对窗共用一份清单(config.py 字段与此同序,增删键三处只改这里)。
ACCOUNT_MAP_KEYS = (
    "revenue_acc",
    "ar_acc",
    "vat_output_acc",
    "fallback_acc",
    "ap_acc",
    "vat_input_acc",
)


@dataclass
class PairResult:
    ok: bool
    error: str = ""
    error_key: str = ""
    error_fmt: dict = field(default_factory=dict)
    company: str = ""
    tax_id: str = ""
    writable: bool = False
    account_sets: int = 0


def _dir_missing_spec(account_dir: str) -> tuple[str, dict]:
    """账套数据目录够不到 → 人话提示。

    数据在某盘符(如 S:\\…)而本机没这个盘(Express 把账套路径写死成服务器本地盘,做账那台
    映射了才够得到)→ 指路(装到做账那台 / 映射该盘);其余情况退通用「目录不存在」。
    """
    import os

    if not account_dir:
        return "pair_err_dir_empty", {}
    if len(account_dir) >= 2 and account_dir[1] == ":":
        drive = account_dir[:2].upper()  # 'S:'
        if not os.path.exists(drive + "\\"):  # 该盘本机根本没有 → 针对性指路
            return "pair_err_drive_missing", {"drive": drive}
    return "pair_err_dir_missing", {"dir": account_dir}


def _dir_missing_message(account_dir: str, lang: str) -> str:
    key, fmt = _dir_missing_spec(account_dir)
    return t(key, lang, **fmt)


def _failure(key: str, lang: str, **fmt) -> PairResult:
    return PairResult(False, t(key, lang, **fmt), key, fmt)


def _endpoint_id_from_token(token: str) -> str:
    parts = str(token or "").split("_", 2)
    return parts[1] if len(parts) == 3 else ""


def _connection_identity(heartbeat: dict, token: str) -> dict[str, str]:
    raw = heartbeat.get("connection") if isinstance(heartbeat, dict) else None
    identity = raw if isinstance(raw, dict) else {}
    return {
        "endpoint_id": str(
            identity.get("endpoint_id") or _endpoint_id_from_token(token)
        ).strip(),
        "endpoint_name": str(identity.get("endpoint_name") or "Express").strip(),
        "pearnly_account": str(identity.get("pearnly_account") or "").strip(),
    }


def pair(
    *,
    token: str,
    chosen: AccountSet,
    all_sets: Optional[List[AccountSet]] = None,
    express_root: str,
    backend_url: str = "",
    express_exe: Optional[str] = None,
    express_workdate: str = "",
    enable_autostart: bool = True,
    mapping: Optional[dict] = None,
    authorized_roots: Optional[List[str]] = None,
    lang: str = "",
    config: Optional[CompanionConfig] = None,
    profile_store: Optional[ProfileStore] = None,
) -> PairResult:
    # lang="" 落 t() 内部 normalize() 的默认逻辑(按 Windows 语言),无需在此重复解析。
    cfg = config or CompanionConfig.load()
    code = (token or "").strip()
    if not code:
        if not bool(getattr(cfg, "paired", False)):
            return _failure("pair_err_code_required", lang)
        # 编辑时留空 = 沿用当前密钥和它所属的站点。
        token = str(cfg.bearer_token or "").strip()
        backend_url = backend_url or str(cfg.backend_url or "").strip()
    else:
        token, site = split_pairing_code(code)
        backend_url = backend_url or site
    if not token.startswith("exp_") or not backend_url.startswith(("https://", "http://")):
        return _failure("pair_err_token_format", lang)
    if chosen is None:
        return _failure("pair_err_no_chosen", lang)

    express_root = str(express_root or "").strip()
    account_dir = str(getattr(chosen, "path", "") or "").strip()
    if not account_dir or not Path(account_dir).exists():
        # 账套由网页第一步选（用户 2026-09-26），这里的 chosen 只是界面上看不到的内部默认：它够不到就换成
        # 第一个本机够得到的账套，不因此拦住配对（2026-09-27 真机：默认账套目录不存在 → 配对失败）。
        reachable = next((item for item in (all_sets or []) if str(getattr(item, "path", "") or "").strip()
                          and Path(str(item.path)).exists()), None)
        if reachable is None:
            key, fmt = _dir_missing_spec(account_dir)
            return _failure(key, lang, **fmt)
        chosen, account_dir = reachable, str(reachable.path).strip()
    effective_root = express_root or str(Path(account_dir).parent)
    requested_roots = (
        authorized_roots
        if authorized_roots is not None
        else (getattr(cfg, "authorized_roots", None) or [effective_root])
    )
    roots = list(
        dict.fromkeys(
            str(root).strip().rstrip("\\/")
            for root in requested_roots
            if str(root).strip().rstrip("\\/")
        )
    )
    if not roots:
        roots = [effective_root]
    sets = list(all_sets) if all_sets is not None else []
    if not sets:
        try:
            sets = probe_account_sets(effective_root)
        except Exception:
            log.exception("探测 Express 账套失败")
            return _failure("pair_err_probe_fail", lang)
    seen_sets = {
        ntpath.normcase(ntpath.normpath(str(item.path).replace("/", "\\")))
        for item in sets
    }
    current_root = ntpath.normcase(ntpath.normpath(effective_root.replace("/", "\\")))
    for root in roots:
        normalized_root = ntpath.normcase(ntpath.normpath(root.replace("/", "\\")))
        if normalized_root == current_root:
            continue
        try:
            discovered = probe_account_sets(root)
        except Exception:
            log.warning("授权数据根暂时无法读取:%s", root)
            continue
        for item in discovered:
            identity = ntpath.normcase(
                ntpath.normpath(str(item.path).replace("/", "\\"))
            )
            if identity in seen_sets:
                continue
            seen_sets.add(identity)
            sets.append(item)

    row = int(getattr(chosen, "row", 0) or 0)  # grid 行号:登记表 COMPNAM 升序确定性算出
    # ★唯一键 = 账套 PATH(= 解析后 account_dir),不用 COMPCOD —— COMPCOD 是 SCCOMP 槽位、跨
    # (含已删)行不唯一(多数=DATAT);PATH 才是账套唯一身份,且与端侧 C2 锁定(account_dir)对齐。
    account_set_name = account_dir

    # 校验配对码 + 上报账套列表 + 所选账套整组(连云端 heartbeat)。
    qc_cfg = QueueClientConfig(
        backend_url=backend_url, bearer_token=token, companion_id="pair-probe"
    )
    selected = {
        "account_set": account_set_name,  # = 账套 PATH(唯一键)
        "account_dir": account_dir,
        "account_company": chosen.company,  # 法定公司名(ISINFO.THINAM · PACK 硬闸期望)
        "account_set_row": row,
    }
    # 科目映射(配对窗全自动取值,见 pairing_flow.auto_mapping)· 随配对即时上报 + 存本地。
    _mapping = {k: str((mapping or {}).get(k) or "").strip() for k in ACCOUNT_MAP_KEYS}
    try:
        with QueueClient(qc_cfg) as qc:
            hb = qc.health(
                account_sets=[asdict(a) for a in sets],
                method="dbf",
                selected=selected,
                mapping=_mapping,
            )
    except httpx.HTTPStatusError as e:
        cat = http_status_category(e.response.status_code)
        if cat == "auth":
            return _failure("pair_err_invalid_code", lang)
        if cat == "disabled":
            return _failure("pair_err_disabled", lang)
        if cat == "push_off":
            return _failure("pair_err_push_off", lang)
        return _failure("pair_err_http", lang, code=e.response.status_code)
    except Exception:
        log.exception("配对时连接 Pearnly 失败")
        return _failure("pair_err_network", lang)
    if not hb.get("ok"):
        return _failure("pair_err_rejected", lang)
    identity = _connection_identity(hb, token)
    # account_set 唯一键 = 账套 PATH(端侧选定即权威);云端 config.account_set 由本心跳上报存入,
    # 不反向覆盖(避免旧 DATAT 默认串账套)。

    target = replace(cfg) if profile_store is not None else cfg
    target.update(
        bearer_token=token,
        backend_url=backend_url,
        method="dbf",
        account_dir=account_dir,
        account_set=account_set_name,
        express_root=effective_root,
        authorized_roots=roots,
        express_exe=express_exe
        or str(Path(express_root or Path(account_dir).parent) / "ExpressI.exe"),
        account_company=chosen.company,
        account_set_row=row,
        express_workdate=express_workdate,
        paired=True,
        **identity,
        **_mapping,
    )
    try:
        if profile_store is not None:
            profile_store.save_profile(target)
        else:
            target.save()
    except ProfileStoreError:
        log.exception("保存 Express 连接失败")
        return _failure("pair_err_profile_save", lang)

    if enable_autostart:
        try:
            autostart.enable()
            target.update(autostart=True)
            if profile_store is not None:
                profile_store.save_profile(target)
            else:
                target.save()
        except Exception:
            log.exception("写开机自启失败(忽略 · 不阻断配对)")

    from .logger import activity

    activity("log_paired", lang=target.ui_lang, company=chosen.company or chosen.name)
    return PairResult(
        True,
        company=chosen.company or chosen.name,
        tax_id=chosen.tax_id,
        writable=chosen.writable,
        account_sets=len(sets),
    )


# ── MR.ERP 的小助手成员：只读文件夹（定时任务，2026-10-09）────────────────────────────
# 配对码格式与 Express 相同；配对前先用文件夹同步接口问服务端「这条连接是哪种 ERP」（`probe`，不改任何东西）。
# MR.ERP → 存成只读文件夹的成员（不找 Express、不上报账套、不领推送任务）；其余照 Express 配对。


def _code_parts(code: str, cfg: CompanionConfig) -> tuple[str, str]:
    """配对码 → (密钥, 站点)；编辑时留空 = 沿用当前密钥和它所属的站点。"""
    if not str(code or "").strip():
        if not bool(getattr(cfg, "paired", False)):
            return "", ""
        return str(cfg.bearer_token or "").strip(), str(cfg.backend_url or "").strip()
    return split_pairing_code(code)


def connection_kind(code: str, cfg: Optional[CompanionConfig] = None) -> str:
    """这个配对码属于哪种连接：``mrerp`` / ``express``。问不出（旧服务端、网络、密钥不对）按 Express 走现有配对，
    错误由那边照常提示。"""
    from .folder_client import FolderClient

    token, site = _code_parts(code, cfg or CompanionConfig())
    if not token.startswith("exp_") or not site.startswith(("https://", "http://")):
        return "express"
    client = FolderClient(site, token, timeout=15.0)
    try:
        reply = client.sync({"probe": True})
    except Exception as error:
        log.info("配对前查询连接类型失败，按 Express 配对: %s", error)
        return "express"
    finally:
        client.close()
    return "mrerp" if isinstance(reply, dict) and reply.get("adapter") == "mrerp" else "express"


def pair_folder(
    *,
    token: str,
    enable_autostart: bool = True,
    lang: str = "",
    config: Optional[CompanionConfig] = None,
    profile_store: Optional[ProfileStore] = None,
) -> PairResult:
    """MR.ERP 的小助手成员：确认配对码有效、是 MR.ERP 连接，存成只读文件夹的成员。"""
    from .folder_client import FolderClient, Refused

    cfg = config or CompanionConfig.load()
    token, backend_url = _code_parts(token, cfg)
    if not token:
        return _failure("pair_err_code_required", lang)
    if not token.startswith("exp_") or not backend_url.startswith(("https://", "http://")):
        return _failure("pair_err_token_format", lang)
    client = FolderClient(backend_url, token, timeout=15.0)
    try:
        reply = client.sync({"probe": True})
    except Refused:
        return _failure("pair_err_invalid_code", lang)
    except httpx.HTTPStatusError as e:
        return _failure("pair_err_http", lang, code=e.response.status_code)
    except Exception:
        log.exception("配对时连接 Pearnly 失败")
        return _failure("pair_err_network", lang)
    finally:
        client.close()
    if not isinstance(reply, dict) or not reply.get("ok") or reply.get("adapter") != "mrerp":
        return _failure("pair_err_rejected", lang)
    label = str(reply.get("label") or "MR.ERP").strip()[:80] or "MR.ERP"
    target = replace(cfg) if profile_store is not None else cfg
    target.update(
        bearer_token=token,
        backend_url=backend_url,
        kind="folder",
        paired=True,
        endpoint_id=_endpoint_id_from_token(token),
        endpoint_name=label,
    )
    try:
        if profile_store is not None:
            profile_store.save_profile(target)
        else:
            target.save()
    except ProfileStoreError:
        log.exception("保存 MR.ERP 小助手成员失败")
        return _failure("pair_err_profile_save", lang)
    if enable_autostart:
        try:
            autostart.enable()
        except Exception:
            log.exception("写开机自启失败(忽略 · 不阻断配对)")
    from .logger import activity

    activity("log_paired", lang=target.ui_lang, company=label)
    return PairResult(True, company=label)
