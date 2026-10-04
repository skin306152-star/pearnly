"""Download the exact attachments selected in the confirmed draft."""
from pearnly_dms.intake import scan as id_scan
from pearnly_dms.line import out

KINDS = (("id_card", "สำเนาบัตรประชาชน", "idcard.jpg"),
         ("slip", "ใบโอนเงินจอง", "slip.jpg"))


def download(qa, *, missing=None):
    downloaded, failed = [], []
    for key, display, filename in KINDS:
        mid = (qa.get("files") or {}).get(f"{key}_mid")
        if not mid or (missing is not None and display not in missing):
            continue
        if str(mid).startswith(id_scan.PREFIX):
            # 扫描页直传的身份证在本站点，不在 LINE（2026-10-05：曾当成 LINE 消息去下载，附件失败）。
            content = id_scan.content(mid)
        else:
            content = out.download_content(mid)
        if not content:
            failed.append({"display_name": display, "error": "line content download failed"})
            continue
        downloaded.append({"display_name": display, "filename": filename,
                           "content_type": "image/png" if content[:4] == b"\x89PNG" else "image/jpeg",
                           "content": content})
    return downloaded, failed


def expected_names(qa):
    return {display for key, display, _ in KINDS if (qa.get("files") or {}).get(f"{key}_mid")}
