"""Recover only an expired pre-submission claim; never replay a confirmed order."""
import logging
import secrets

import frappe
from pearnly_core import line_session as store
from pearnly_dms.booking.conversation import qa_cards
from pearnly_dms.line import cards, out
from pearnly_dms.line.identity import current

logger = logging.getLogger(__name__)


def recover(line_user_id):
    session = store.get_session(line_user_id) or {}
    payload = session.get("payload") or {}
    stamp = payload.get("booking_claimed_at")
    if (session.get("state") != "booking_review" or not stamp or payload.get("nonce")
            or not payload.get("booking_claim") or payload.get("booking_attempt")):
        return False
    if (frappe.utils.get_datetime() - frappe.utils.get_datetime(stamp)).total_seconds() <= 660:
        return False  # Worker timeout is 600 seconds, including external writes.
    if not current(line_user_id):
        return False
    nonce = secrets.token_hex(8)
    if not store.replace_claimed_booking_payload(line_user_id, payload["booking_claim"],
                                                "booking_review", {**payload, "nonce": nonce}):
        return False
    from pearnly_dms import i18n

    with i18n.line_language(line_user_id):  # 定时任务发的：按收件人的手机语言
        out.send(line_user_id, qa_cards.booking_retry_card(cards.TXT_SYSTEM_ERROR, nonce))
    return True


def schedule_due():
    rows = frappe.db.sql(
        "select line_user_id from `tabPearnly LINE Session` where state='booking_review' "
        "and JSON_EXTRACT(payload, '$.booking_claimed_at') is not null "
        "and JSON_EXTRACT(payload, '$.booking_attempt') is null "
        "and modified < %s order by modified asc limit 50",
        (frappe.utils.add_to_date(minutes=-11),), as_dict=True)
    for row in rows:
        try:
            recover(row.line_user_id)
        except Exception:
            logger.warning("Pre-submission claim remains retained", exc_info=True)
