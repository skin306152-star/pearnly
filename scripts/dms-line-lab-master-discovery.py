"""Read only the test DMS's native admin navigation and save a private snapshot.

No native form submissions, master writes, or account permission changes.
"""
import json
import os
from urllib.parse import urljoin

import frappe
from bs4 import BeautifulSoup

os.chdir("/home/frappe/frappe-bench/sites")
frappe.init(site="dms-line-lab.localhost", sites_path=".")
frappe.connect()
frappe.set_user("Administrator")
from pearnly_dms.intake.service import run_logged_in
from pearnly_dms.ocr.ocr_service import resolve_dms_endpoint

try:
    user = frappe.db.get_value("DMS Operator", {"display_name": "E2E DMS operator"}, "user")
    def read(client, adapter):
        body = client.transport.get(client.base_url + "home/home.php").text
        path = "dms-line-lab.localhost/private/line-lab-master-home.html"
        with open(path, "w") as handle:
            handle.write(body)
        os.chmod(path, 0o600)
        soup = BeautifulSoup(body, "html.parser")
        navigation = soup.select_one("#mmenu ul")
        views = {}
        for kind in ("car", "carpaint"):
            view = client._post_text(kind + "/form.php", {"status": "n"})
            with open("dms-line-lab.localhost/private/line-lab-master-" + kind + ".html", "w") as handle:
                handle.write(view)
            parsed = BeautifulSoup(view, "html.parser")
            views[kind] = {"text": parsed.get_text(" ", strip=True)[:3000],
                           "fields": [{key: tag.get(key) for key in ("name", "id", "type", "value", "required")}
                                      for tag in parsed.select("input,select,textarea")],
                           "scripts": [tag.get("src") for tag in parsed.select("script[src]")],
                           "actions": [tag.get("onclick") for tag in parsed.select("[onclick]")]}
            for tag in parsed.select("script[src]"):
                if "form" in tag["src"] or "mt" in tag["src"]:
                    source = client.transport.get(urljoin(client.base_url + kind + "/view.php", tag["src"])).text
                    basename = tag["src"].rsplit("/", 1)[-1]
                    with open("dms-line-lab.localhost/private/line-lab-master-" + kind + "-" + basename, "w") as handle:
                        handle.write(source)
        return {"ok": True, "views": views}
    result = run_logged_in(resolve_dms_endpoint(user, None), read, authoritative_read=True)
    print(json.dumps(result, ensure_ascii=False))
finally:
    frappe.destroy()
