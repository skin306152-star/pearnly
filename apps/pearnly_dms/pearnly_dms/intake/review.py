"""A changed upstream customer requires a NEW server-owned review/approval."""
import secrets

from pearnly_core import line_session as store
from pearnly_dms.approval import approval_flow
from pearnly_dms.contracts.customer_merge import split
from pearnly_dms.intake import draft
from pearnly_dms.line import cards, out
from pearnly_dms.line.errors import friendly
from pearnly_dms.line.identity import matches
from pearnly_dms import i18n


def conflict_notice(conflict):
    text = friendly("ERR_DMS_CUSTOMER_CHANGED", lang=i18n.line_lang())
    for change in conflict.get("conflicts") or []:
        label = cards.FIELD_LABELS_TH.get(change["field"], change["field"])
        text += "\n" + str(label) + ": " + str(change["old"]) + " → " + str(change["new"])
    return text


def build(user_id, payload, conflict, *, has_admin):
    current, desired = conflict["current"], conflict["desired"]
    diffs = [{"field": key, "old": str(current.get(key) or ""), "new": str(value)}
             for key, value in desired.items() if str(current.get(key) or "") != str(value)]
    fields, addresses = split(desired)
    customer = {**current, **desired}
    _, summary = draft.master_snapshot(customer)
    master_draft, master_summary = draft.master_snapshot(current)
    nonce = secrets.token_hex(8)
    updated = {key: value for key, value in payload.items()
               if key not in {"dms_retry", "candidates", "approval", "display_diffs"}}
    updated.update(nonce=nonce, customer_id=conflict["customer_id"], scenario="exact_diff",
                   customer_baseline=current, draft=customer, summary=summary, field_diffs=diffs,
                   write_fields=fields, write_addresses=addresses,
                   master_draft=master_draft, master_summary=master_summary)
    if diffs:
        display = draft.display_diffs(diffs, {})
        card, approval = approval_flow.exact_diff_card(user_id, display, has_admin, nonce, summary)
        updated.update(display_diffs=display, has_admin=has_admin, approval=approval)
    else:
        card = cards.same_customer_card(summary, nonce)
    return updated, card


def reopen(binding, line_user_id, payload, result, *, reply_token="", expected=None):
    if not matches(binding, line_user_id):
        return False
    current = store.get_session(line_user_id)
    expected = expected or {"state": "reviewing", "payload": {**payload, "nonce": None}}
    if (not current or current.get("state") != expected.get("state")
            or current.get("payload") != expected.get("payload")):
        return False
    from pearnly_dms.ocr.ocr_service import resolve_dms_endpoint
    endpoint = resolve_dms_endpoint(binding["user_id"], payload.get("endpoint_id"))
    updated, card = build(binding["user_id"], payload,
        result["response_body"]["customer_conflict"], has_admin=draft.has_admin_creds(endpoint))
    if not store.replace_session_snapshot(line_user_id, current, "reviewing", updated):
        return False
    out.push_or_reply_text(line_user_id, friendly(result["error_code"], lang=i18n.line_lang()), reply_token)
    out.send(line_user_id, card)
    return True
