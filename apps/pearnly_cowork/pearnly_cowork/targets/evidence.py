"""Source proof predicates over injected, already-scoped database rows.

The caller supplies the latest endpoint refresh and requested snapshot joined
to its current head under the endpoint binding lock. Row-shaped client JSON is
not proof. This module never queries, refreshes or marks a projection fresh.
"""
from __future__ import annotations
import json
import uuid
from typing import Any, Mapping
from pearnly_core.express_rules.targets.identity import normalize_express_account_key
ENDPOINT_SCOPE_KEY = '@endpoint'
CATALOG_REFRESH_REQUIRED = 'catalog_refresh_required'
CATALOG_REFRESH_INVALID = 'catalog_refresh_invalid'

def _adapter(value: Any) -> str:
    return str(value or '').strip().lower()

def _account_identity(adapter: str, value: Any) -> str:
    raw = str(value or '').strip()
    return normalize_express_account_key(raw) if adapter == 'express' else raw[:500]

def _root_identity(value: Any) -> str:
    return normalize_express_account_key(value)

def _revision(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None

def _request_id(value: Any) -> str | None:
    try:
        return str(uuid.UUID(str(value)))
    except (AttributeError, TypeError, ValueError):
        return None

def _result(*, ok: bool, proof_required: bool, reason: str, account_set_key: str, root_key: str | None, request_id: str | None=None, revision: int | None=None) -> dict[str, Any]:
    return {'ok': ok, 'proof_required': proof_required, 'error_code': None if ok else CATALOG_REFRESH_REQUIRED if reason == 'proof_missing' else CATALOG_REFRESH_INVALID, 'reason': reason, 'account_set_key': account_set_key, 'root_key': root_key, 'request_id': request_id, 'revision': revision}

def _receipt_result(*, ok: bool, reason: str, request_id: str | None, revision: int | None) -> dict[str, Any]:
    return {'ok': ok, 'reason': reason, 'request_id': request_id, 'revision': revision}

def _account_sets(value: Any) -> list[Mapping[str, Any]] | None:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return None
    if not isinstance(value, list):
        return None
    return [row for row in value if isinstance(row, Mapping)]

def _selected_account(rows: list[Mapping[str, Any]], adapter: str, selected_key: str) -> Mapping[str, Any] | None:
    for row in rows:
        if _account_identity(adapter, row.get('source_id')) == selected_key:
            return row
    return None

def _normalized_roots(account_roots: Mapping[str, Any] | None) -> dict[str, str]:
    """数据目录 → 程序目录(两侧都归一化)· 只保留成对给出的行。"""
    roots: dict[str, str] = {}
    for (key, value) in (account_roots or {}).items():
        normalized_key = _account_identity('express', key)
        normalized_root = _root_identity(value)
        if normalized_key and normalized_root:
            roots.setdefault(normalized_key, normalized_root)
    return roots

def _snapshot_reason(snapshot, *, adapter: str, selected_key: str, selected_root: str, expected_root: str, revision: int | None) -> tuple[str, str | None]:
    """所选账套在这份目录里是否可用；`revision` 为 None 时只认「当前这一份」，不要求刚刷新过。"""
    root_result = selected_root or None
    if not snapshot:
        return ('snapshot_not_found', root_result)
    if revision is not None and str(snapshot.get('head_status') or '') != 'fresh':
        return ('projection_not_fresh', root_result)
    if revision is not None and _revision(snapshot.get('head_revision')) != revision:
        return ('snapshot_superseded', root_result)
    if str(snapshot.get('head_snapshot_id') or '') != str(snapshot.get('snapshot_id') or ''):
        return ('snapshot_superseded', root_result)
    if _adapter(snapshot.get('adapter')) != adapter:
        return ('adapter_mismatch', root_result)
    account_sets = _account_sets(snapshot.get('account_sets'))
    if account_sets is None:
        return ('snapshot_invalid', root_result)
    selected = _selected_account(account_sets, adapter, selected_key)
    if selected is None:
        return ('account_not_found', root_result)
    if selected.get('active', True) is False:
        return ('account_inactive', root_result)
    if adapter != 'express':
        return ('', root_result)
    attributes = selected.get('attributes') if isinstance(selected.get('attributes'), Mapping) else {}
    snapshot_root = _root_identity(attributes.get('root'))
    root_result = selected_root or snapshot_root or expected_root or None
    if attributes.get('writable', True) is False:
        return ('account_not_writable', root_result)
    if snapshot_root and selected_root and (snapshot_root != selected_root):
        return ('root_mismatch', root_result)
    if snapshot_root and expected_root and (snapshot_root != expected_root):
        return ('root_mismatch', root_result)
    return ('', root_result)

def _validate_current_snapshot(*, snapshot, adapter: str, selected_key: str, selected_root: str, expected_root: str='') -> dict[str, Any]:
    """网页用服务端存着的目录选（用户 2026-09-27：打开弹窗不再现扫，改手动「重新读取」）：
    不再要求一张刚刷新的回执，只要所选账套在当前这份目录里、可用可写、程序目录对得上。
    真正写入前小助手还会对照 Express 登记表当场再核一次。"""
    (reason, root_result) = _snapshot_reason(snapshot, adapter=adapter, selected_key=selected_key, selected_root=selected_root, expected_root=expected_root, revision=None)
    if reason:
        return _result(ok=False, proof_required=True, reason=reason, account_set_key=selected_key, root_key=root_result)
    return _result(ok=True, proof_required=True, reason='current_snapshot', account_set_key=selected_key, root_key=root_result)

def _validate_proof_rows(*, latest_refresh, snapshot, tenant_id: str, endpoint_id: str, adapter: str, selected_key: str, selected_root: str, expected_root: str='', request_id: str, revision: int) -> dict[str, Any]:
    root_result = selected_root or None
    refresh = latest_refresh
    if not refresh:
        reason = 'refresh_not_found'
    elif str(refresh.get('id') or '') != request_id:
        reason = 'refresh_superseded'
    elif str(refresh.get('status') or '') != 'succeeded':
        reason = 'refresh_not_succeeded'
    elif str(refresh.get('account_set_key') or '') != ENDPOINT_SCOPE_KEY:
        reason = 'refresh_scope_mismatch'
    elif _adapter(refresh.get('adapter')) != adapter:
        reason = 'adapter_mismatch'
    elif _revision(refresh.get('result_revision')) != revision:
        reason = 'revision_mismatch'
    else:
        reason = ''
    if reason:
        return _result(ok=False, proof_required=True, reason=reason, account_set_key=selected_key, root_key=root_result, request_id=request_id, revision=revision)
    (reason, root_result) = _snapshot_reason(snapshot, adapter=adapter, selected_key=selected_key, selected_root=selected_root, expected_root=expected_root, revision=revision)
    if reason:
        return _result(ok=False, proof_required=True, reason=reason, account_set_key=selected_key, root_key=root_result, request_id=request_id, revision=revision)
    return _result(ok=True, proof_required=True, reason='validated_snapshot', account_set_key=selected_key, root_key=root_result, request_id=request_id, revision=revision)

def validate_selection(*, tenant_id: str, user_id: str, endpoint_id: str, adapter: str, selected_account_set_key: Any, bound_account_set_key: Any, selected_root_key: Any=None, bound_root_key: Any=None, request_id: Any=None, revision: Any=None, account_roots: Mapping[str, Any] | None=None, latest_refresh=None, snapshot=None) -> dict[str, Any]:
    """Validate already-authorized, scoped refresh and snapshot rows; no cursor or IO."""
    adapter = _adapter(adapter)
    selected_key = _account_identity(adapter, selected_account_set_key)
    bound_key = _account_identity(adapter, bound_account_set_key)
    selected_root = _root_identity(selected_root_key) if adapter == 'express' else ''
    bound_root = _root_identity(bound_root_key) if adapter == 'express' else ''
    if adapter == 'express':
        reported_roots = _normalized_roots(account_roots)
        selected_root = selected_root or reported_roots.get(selected_key, '')
        bound_root = bound_root or reported_roots.get(bound_key, '')
        root_matches_default = not (selected_root and bound_root and (selected_root != bound_root))
    else:
        root_matches_default = True
    is_bound_default = bool(selected_key and selected_key == bound_key and root_matches_default)
    root_result = selected_root or None
    if adapter not in {'mrerp', 'express'} or not selected_key:
        return _result(ok=False, proof_required=not is_bound_default, reason='selection_invalid', account_set_key=selected_key, root_key=root_result)
    if is_bound_default:
        return _result(ok=True, proof_required=False, reason='bound_default', account_set_key=selected_key, root_key=root_result)
    if request_id in (None, '') and revision in (None, ''):
        if not str(tenant_id or '').strip() or not str(user_id or '').strip() or not str(endpoint_id or '').strip():
            return _result(ok=False, proof_required=True, reason='context_invalid', account_set_key=selected_key, root_key=root_result)
        # 年度 = Express 数据文件夹，一条连接下可选多个（小助手授权过的才会出现在目录里），
        # 所以不再钉死连接绑定的那个文件夹；账套属于哪个文件夹以目录为准，写入前小助手再核。
        return _validate_current_snapshot(snapshot=snapshot, adapter=adapter, selected_key=selected_key, selected_root=selected_root)
    if request_id in (None, '') or revision in (None, ''):
        return _result(ok=False, proof_required=True, reason='proof_missing', account_set_key=selected_key, root_key=root_result)
    validated_request_id = _request_id(request_id)
    validated_revision = _revision(revision)
    if not validated_request_id or validated_revision is None:
        return _result(ok=False, proof_required=True, reason='proof_malformed', account_set_key=selected_key, root_key=root_result)
    tenant_id = str(tenant_id or '').strip()
    user_id = str(user_id or '').strip()
    endpoint_id = str(endpoint_id or '').strip()
    if not tenant_id or not user_id or (not endpoint_id):
        return _result(ok=False, proof_required=True, reason='context_invalid', account_set_key=selected_key, root_key=root_result, request_id=validated_request_id, revision=validated_revision)
    proof_kwargs = {'tenant_id': tenant_id, 'endpoint_id': endpoint_id, 'adapter': adapter, 'selected_key': selected_key, 'selected_root': selected_root, 'expected_root': bound_root, 'request_id': validated_request_id, 'revision': validated_revision}
    return _validate_proof_rows(latest_refresh=latest_refresh, snapshot=snapshot, **proof_kwargs)

def validate_refresh_receipt(*, tenant_id: str, user_id: str, endpoint_id: str, adapter: str, request_id: Any, request_revision: Any, catalog_revision: Any, latest_refresh=None) -> dict[str, Any]:
    """Match one succeeded refresh to the catalog snapshot returned to the caller."""
    validated_request_id = _request_id(request_id)
    validated_request_revision = _revision(request_revision)
    validated_catalog_revision = _revision(catalog_revision)
    tenant_id = str(tenant_id or '').strip()
    user_id = str(user_id or '').strip()
    endpoint_id = str(endpoint_id or '').strip()
    adapter = _adapter(adapter)
    if not validated_request_id or validated_request_revision is None or validated_catalog_revision is None or (not tenant_id) or (not user_id) or (not endpoint_id) or (adapter not in {'mrerp', 'express'}):
        return _receipt_result(ok=False, reason='receipt_malformed', request_id=validated_request_id, revision=validated_request_revision)
    if validated_catalog_revision != validated_request_revision:
        return _receipt_result(ok=False, reason='revision_mismatch', request_id=validated_request_id, revision=validated_request_revision)
    refresh = latest_refresh
    if not refresh:
        reason = 'refresh_not_found'
    elif str(refresh.get('id') or '') != validated_request_id:
        reason = 'refresh_superseded'
    elif str(refresh.get('status') or '') != 'succeeded':
        reason = 'refresh_not_succeeded'
    elif str(refresh.get('account_set_key') or '') != ENDPOINT_SCOPE_KEY:
        reason = 'refresh_scope_mismatch'
    elif _adapter(refresh.get('adapter')) != adapter:
        reason = 'adapter_mismatch'
    elif _revision(refresh.get('result_revision')) != validated_request_revision:
        reason = 'revision_mismatch'
    else:
        reason = ''
    if reason:
        return _receipt_result(ok=False, reason=reason, request_id=validated_request_id, revision=validated_request_revision)
    if str(refresh.get('head_status') or '') != 'fresh':
        reason = 'projection_not_fresh'
    elif _revision(refresh.get('head_revision')) != validated_request_revision:
        reason = 'snapshot_superseded'
    elif _adapter(refresh.get('snapshot_adapter')) != adapter:
        reason = 'adapter_mismatch'
    else:
        reason = ''
    return _receipt_result(ok=not reason, reason=reason or 'validated_snapshot', request_id=validated_request_id, revision=validated_request_revision)
__all__ = ['CATALOG_REFRESH_INVALID', 'CATALOG_REFRESH_REQUIRED', 'validate_refresh_receipt', 'validate_selection']
