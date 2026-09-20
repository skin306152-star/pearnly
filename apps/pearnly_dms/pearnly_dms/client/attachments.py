"""Append files using a fresh native form and verify after every write.

The DMS edit endpoint replaces the whole document, including existing files.
Never reuse the first form for a later upload, or replay an ambiguous upload
before checking whether its display name already exists on this exact order.
"""
from pearnly_dms.contracts.client_base import DmsClientError


def _form(client, booking_id):
    fields = client._parse_form_defaults(client._post_text(
        "drfcbc/form.php", {"status": "e", "id": str(booking_id)}))
    if fields.get("stsel") != "e" or fields.get("idsel") != str(booking_id):
        raise DmsClientError("Booking attachment form identity mismatch", "ERR_DMS_IMPORT")
    return fields


def _names(fields):
    return {value for key, value in fields.items() if key.startswith("fulcurrname")}


def attach(client, *, booking_id, files):
    attached, failed = 0, []
    for item in files or []:
        display = str(item.get("display_name") or "")[:150]
        try:
            base = _form(client, booking_id)
            if display in _names(base):
                attached += 1
                continue
            # Every upload includes files added by the previous upload and any
            # intervening native edits. Their hidden IDs must be preserved.
            try:
                client.transport.post(
                    client._url("drfcbc/edit.php"),
                    data={**base, "fulnewname[]": display},
                    files={"fulnew[]": (item["filename"], item["content"], item["content_type"])},
                    timeout_ms=120000,
                )
            except Exception:
                pass  # A timeout may follow a committed upload. Read, never replay.
            after = _form(client, booking_id)
            if after.get("txtdocno") != base.get("txtdocno"):
                raise DmsClientError("Attachment readback document changed", "ERR_DMS_IMPORT")
            if not _names(base).issubset(_names(after)):
                raise DmsClientError("Attachment readback lost an existing file", "ERR_DMS_IMPORT")
            if display not in _names(after):
                raise DmsClientError("Attachment missing in readback", "ERR_DMS_IMPORT")
            attached += 1
        except Exception as exc:
            # Never expose arbitrary remote HTML or customer fields in logs.
            failed.append({"display_name": display, "error": type(exc).__name__})
    return {"ok": not failed, "attached": attached, "failed": failed}
