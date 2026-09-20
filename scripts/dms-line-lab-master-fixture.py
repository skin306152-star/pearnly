"""Explicit test-only native DMS master CRUD, outside the product write path.

Requires the isolated lab's private test connection and --execute-lab-writes.
Only this script's journaled E2E260920 model/paint may be edited or removed.
The normal product admin-read guard remains enabled and is never overridden.
"""
import argparse
import json
import os
from pathlib import Path

from bs4 import BeautifulSoup
import frappe

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("phase", choices=("create", "resume-model", "rename", "disable", "delete", "read"))
parser.add_argument("--execute-lab-writes", action="store_true")
args = parser.parse_args()
if args.phase != "read" and not args.execute_lab_writes:
    parser.error("External test master mutation must be explicitly selected")

SITE = "dms-line-lab.localhost"
MARKER = "E2E260920"
os.chdir("/home/frappe/frappe-bench/sites")
frappe.init(site=SITE, sites_path=".")
frappe.connect()
frappe.set_user("Administrator")
from pearnly_dms.adapter.session import MrerpDmsAdapter

journal_path = Path(SITE) / "private/line-lab-master-fixture.json"
fixture = json.loads(Path("/lab-private/external-fixture.json").read_text())
assert fixture["source_site"] == "testdms.pearnly.com"
endpoint = fixture["endpoint"]
assert endpoint["admin_username"] and endpoint["admin_password"]
journal = json.loads(journal_path.read_text()) if journal_path.exists() else {"marker": MARKER, "steps": []}
assert journal["marker"] == MARKER

def persist():
    journal_path.write_text(json.dumps(journal, ensure_ascii=False, indent=2))
    journal_path.chmod(0o600)

def form(client, kind, status="n", record_id=""):
    markup = client._post_text(kind + "/form.php", {"status": status, "id": record_id})
    soup = BeautifulSoup(markup, "html.parser")
    values = {}
    for tag in soup.select("form input[name],form select[name],form textarea[name]"):
        if tag.get("type") in ("file", "button", "submit", "reset"):
            continue
        if tag.get("type") in ("checkbox", "radio") and not tag.has_attr("checked"):
            continue
        value = tag.get("value", "")
        if tag.name == "select":
            option = tag.select_one("option[selected]") or tag.select_one("option")
            value = option.get("value", "") if option else ""
        if tag.name == "textarea":
            value = tag.get_text()
        key = tag["name"]
        if key.endswith("[]"):
            values.setdefault(key, []).append(value)
        else:
            values[key] = value
    assert values.get("stsel") == status
    return values

def rows(client, kind):
    client._post_text(kind + "/view.php", {"idmenu": "3" if kind == "car" else "4", "menulv": "2"})
    markup = client._post_text(kind + "/component/showdata.php", {
        "sdtamt": "100", "sdtpage": "1", "sd": MARKER,
        "ftd": "1", "selcolsort": "1", "selcolsorttype": "1",
    })
    assert not markup.lstrip().startswith("err::")
    return [{"id": tag["data-val"], "text": tag.get_text(" ", strip=True)}
            for tag in BeautifulSoup(markup, "html.parser").select("[data-val]")
            if MARKER in tag.get_text()]

def mutate(client, kind, operation, values):
    code = values["txtmodelcode" if kind == "car" else "txtcarpaintcode"]
    assert code == MARKER, "Never mutate a master not owned by this test"
    if operation != "new":
        assert values["idsel"] == journal[kind]
    event = {"kind": kind, "operation": operation, "state": "sent", "id": values.get("idsel")}
    journal["steps"].append(event)
    persist()  # An interrupted/unknown result cannot be replayed blindly.
    # PHP FormData arrays must be repeated/indexed fields, never Python list text.
    encoded = {}
    for key, value in values.items():
        if isinstance(value, list):
            for index, item in enumerate(value):
                encoded[f"{key[:-2]}[{index}]"] = item
        else:
            encoded[key] = value
    result = client._post_text(kind + "/" + operation + ".php", encoded)
    event.update(state="response", response=result[:1000])
    persist()
    assert not result.lstrip().startswith("err::"), (kind, operation, result[:300])

try:
    # A dedicated explicit test writer logs in as the existing test administrator.
    # This is NOT authoritative_read_session and cannot be called by product code.
    with MrerpDmsAdapter(system_url=endpoint["system_url"], username=endpoint["admin_username"],
                         password=endpoint["admin_password"]) as adapter:
        adapter.login()
        client = adapter.client()
        if args.phase in ("create", "resume-model"):
            if args.phase == "create":
                assert not journal["steps"], "Read/reconcile the existing journal before any further creation"
                assert not rows(client, "car") and not rows(client, "carpaint")
                paint = form(client, "carpaint")
                paint.update(txtcarpaintcode=MARKER, txtcarpaintname="สีทดสอบ E2E260920", txtcarpaintengname="E2E260920 paint")
                mutate(client, "carpaint", "new", paint)
                found = rows(client, "carpaint")
                assert len(found) == 1
                journal["carpaint"] = found[0]["id"]
                persist()
            else:
                last = journal["steps"][-1]
                assert last["kind"] == "car" and last["operation"] == "new"
                assert last["state"] == "response" and last["response"].strip().startswith("err::")
                assert not rows(client, "car"), "A present or unknown model cannot be replayed"
                assert any(row["id"] == journal["carpaint"] for row in rows(client, "carpaint"))
            client._post_text("car/view.php", {"idmenu": "3", "menulv": "2"})
            # Read an existing model only for valid required classification IDs.
            # No source model is modified; its prices, child tables and identity
            # are not copied into the synthetic model.
            reference = form(client, "car", "v", "2")
            vehicle = form(client, "car")
            for key in ("carbrandval", "txtcarbrand", "enginenoval", "txtengineno", "typecarval", "txttypecar",
                        "subtypecarval", "txtsubtypecar", "typecardescval", "txttypecardesc", "cargearval",
                        "txtcargear", "gradeval", "txtgrade", "enginepowerval", "txtenginepower"):
                assert reference.get(key), key
                vehicle[key] = reference[key]
            vehicle.update(txtmodelcode=MARKER, txtmodelname="E2E260920 model", txtdescmodelname="รุ่นทดสอบ E2E260920",
                           txtcost="80", txtprice="100", txtpriceincvat="107", txtpriceincvatdel="107",
                           txtmanuyear="2026")
            vehicle["chkcarpaint[]"] = [journal["carpaint"]]
            mutate(client, "car", "new", vehicle)
            found = rows(client, "car")
            assert len(found) == 1
            journal["car"] = found[0]["id"]
            persist()
        elif args.phase == "rename":
            for kind, field, value in (("car", "txtmodelname", "E2E260920 renamed"),
                                       ("carpaint", "txtcarpaintname", "สีใหม่ E2E260920")):
                values = form(client, kind, "e", journal[kind])
                values[field] = value
                mutate(client, kind, "edit", values)
                assert form(client, kind, "v", journal[kind])[field] == value
        elif args.phase == "disable":
            values = form(client, "car", "e", journal["car"])
            values["selstatuscancel"] = "1"
            mutate(client, "car", "edit", values)
            assert form(client, "car", "v", journal["car"])["selstatuscancel"] == "1"
        elif args.phase == "delete":
            for kind in ("car", "carpaint"):
                values = form(client, kind, "d", journal[kind])
                mutate(client, kind, "del", values)
                assert not rows(client, kind), "Deletion must be independently read back"
        result = {kind: rows(client, kind) for kind in ("car", "carpaint")}
        journal.setdefault("readbacks", []).append({"phase": args.phase, "result": result})
        persist()
        print(json.dumps({"phase": args.phase, "owned_masters": result}, ensure_ascii=False))
finally:
    frappe.destroy()
