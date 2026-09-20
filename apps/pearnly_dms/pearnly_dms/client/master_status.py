"""Native directory status. Booking pickers also return cancelled entries.

Read the paginated native list in the same authoritative session and join by ID.
Unknown shapes fail closed; they never turn into an empty or active catalogue.
"""
from html.parser import HTMLParser

from pearnly_dms.contracts.client_base import DmsClientError


class _StatusRows(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = {}
        self.level = 0
        self.row = None
        self.row_level = 0
        self.in_status = False
        self.status = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "div":
            self.level += 1
            if attrs.get("onclick", "").startswith("ctllistdata(") and attrs.get("data-val"):
                self.row, self.row_level = attrs["data-val"], self.level
                self.status = None
        if self.row and tag == "p" and self.status is None:
            self.in_status, self.status = True, ""

    def handle_data(self, data):
        if self.in_status:
            self.status += data

    def handle_endtag(self, tag):
        if tag == "p":
            self.in_status = False
        if tag == "div":
            if self.row and self.level == self.row_level:
                self.rows[self.row] = (self.status or "").strip()
                self.row = None
            self.level -= 1


def active_models(client, candidates, *, page_size=200, max_pages=20):
    return _active_rows(client, candidates, 'car', '3', '2', page_size, max_pages)


def active_company_banks(client, candidates, *, page_size=200, max_pages=20):
    return _active_rows(client, candidates, 'bank', '31', '3', page_size, max_pages)


def _active_rows(client, candidates, kind, menu, level, page_size, max_pages):
    if not candidates:
        return []
    client._post_text(kind + '/view.php', {"idmenu": menu, "menulv": level})
    statuses = {}
    for page in range(1, max_pages + 1):
        markup = client._post_text(kind + '/component/showdata.php', {
            "sdtamt": str(page_size), "sdtpage": str(page), "sd": "", "ftd": "1",
            "selcolsort": "1", "selcolsorttype": "1",
        })
        parser = _StatusRows()
        parser.feed(markup)
        rows = parser.rows
        if markup.lstrip().startswith("err::") or set(rows) & set(statuses):
            raise DmsClientError(f"DMS {kind} status paging failed", "ERR_DMS_MASTER_UNAVAILABLE")
        statuses.update(rows)
        if len(rows) < page_size:
            break
    else:
        raise DmsClientError(f"DMS {kind} status page limit reached", "ERR_DMS_MASTER_UNAVAILABLE")
    if any(str(row[0]) not in statuses for row in candidates):
        raise DmsClientError(f"DMS {kind} status catalogue is incomplete", "ERR_DMS_MASTER_UNAVAILABLE")
    if any(value not in {"-", "ยกเลิก"} for value in statuses.values()):
        raise DmsClientError(f"DMS {kind} status shape changed", "ERR_DMS_MASTER_UNAVAILABLE")
    return [row for row in candidates if statuses[str(row[0])] == "-"]
