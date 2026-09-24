"""No-IO business reads from one complete generation; never a live fallback."""
import copy
import re

from pearnly_dms.contracts.client_base import DmsClientError
from pearnly_dms.contracts.geo_matching import resolve
from pearnly_dms.contracts.id_validate import normalize_thai_id

from . import store


class Directory:
    def __init__(self, endpoint):
        self.endpoint = endpoint
        self.snapshot = store.read(endpoint, "directory")
        self.data = self.snapshot["data"]
        self.geo = None

    def metadata(self):
        return {k: v for k, v in self.snapshot.items() if k != "data"}

    def masters(self):
        return {**copy.deepcopy(self.data["masters"]), "_snapshot": {
            k: self.snapshot[k] for k in ("as_of", "generation", "stale")}}

    def paints(self, car_id):
        if str(car_id) not in self.data["paints"]:
            raise DmsClientError("Car is absent from the complete snapshot", "ERR_DMS_MASTER_CHANGED")
        return copy.deepcopy(self.data["paints"][str(car_id)])

    def list_prefixes(self):
        return copy.deepcopy(self.data["masters"]["prefixes"])

    def list_geo(self, level, parent_id=""):
        if level not in ("provinces", "districts", "subdistricts", "zipcodes"):
            raise DmsClientError("Unknown address level", "ERR_DMS_MASTER_UNMATCHED")
        parent = "" if level == "provinces" else str(parent_id or "")
        if self.geo is None:
            snapshot = store.read(self.endpoint, "geo", max_age=3 * 86400, refresh_interval=86400)
            self.geo = snapshot["data"]
            self.snapshot.update(geo_as_of=snapshot["as_of"], geo_stale=snapshot["stale"],
                                 geo_generation=snapshot["generation"])
        return copy.deepcopy(self.geo[level].get(parent, []))

    def resolve_address(self, address):
        return resolve(address, self.list_geo("provinces"), self.list_geo)

    def read_customer(self, customer_id):
        row = self.data["customers"].get(str(customer_id))
        if row is None:
            # The caller selected a specific customer, not a new-customer search.
            raise DmsClientError("Selected customer is absent from snapshot", "ERR_DMS_CUSTOMER_LOOKUP")
        return copy.deepcopy(row)

    def lookup_customer(self, people_id, *, fallback_customer_ids=None):
        target = normalize_thai_id(people_id)
        if not re.fullmatch(r"[0-9]{13}", target):
            raise DmsClientError("Full customer identity required", "ERR_DMS_CUSTOMER_LOOKUP")
        # A recent native ledger hint is not proof of identity, but ignoring a
        # customer absent from this generation can falsely offer "new customer"
        # after an optional write-through failure or initial cache bootstrap.
        # Wait for the next complete scan; never restore foreground live reads.
        if any(str(cid) not in self.data["customers"] for cid in (fallback_customer_ids or []) if cid):
            raise store.SnapshotUnavailable("ERR_DMS_SNAPSHOT_PENDING")
        matches = [(cid, row) for cid, row in self.data["customers"].items()
                   if normalize_thai_id(row.get("people_id", "")) == target]
        if len(matches) > 1:
            raise DmsClientError("Ambiguous customer identity", "ERR_DMS_CUSTOMER_LOOKUP")
        if not matches:
            return {"found": False, "customer_id": None, "fields": {}}
        cid, fields = matches[0]
        return {"found": True, "customer_id": cid, "fields": copy.deepcopy(fields)}

    def search_customers_detailed(self, text, limit=10):
        needle = str(text or "").strip().casefold()
        if not needle:
            return []
        matches = []
        for cid, row in self.data["customers"].items():
            if any(needle in str(row.get(key, "")).casefold() for key in ("name", "people_id", "cuscode")):
                matches.append({"customer_id": cid, **{k: str(row.get(k, "")) for k in ("name", "people_id", "cuscode")}})
        return matches[:max(1, min(int(limit), 100))]
