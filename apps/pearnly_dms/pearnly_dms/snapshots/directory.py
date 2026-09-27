"""No-IO reads of independently published datasets; never a live fallback.

客户：完整客户组没发布前（首次读全档，ris 约 69 分钟），用列表阶段先发布的客户索引加上
已核实写回的客户（`store.customer_overlays`）作答：查无此人可以直接说；查到了但详情还没读到，
抛 `CustomerDetailPending(编号)`，由调用方单独读这一条写回后再来（`adapter/snapshot_writeback`）。
"""
import copy
import re

from pearnly_dms.contracts.client_base import DmsClientError
from pearnly_dms.contracts.geo_matching import resolve
from pearnly_dms.contracts.id_validate import normalize_thai_id

from . import store


class CustomerDetailPending(store.SnapshotUnavailable):
    """索引里有这个客户，但完整资料还没读到：调用方可以只读这一条再重试。"""

    def __init__(self, customer_id):
        super().__init__("ERR_DMS_SNAPSHOT_PENDING")
        self.customer_id = str(customer_id)


class Directory:
    def __init__(self, endpoint):
        self.endpoint = endpoint
        self.snapshot = {}
        self.data = {}
        self.loaded = {}
        self.geo = None

    def _load(self, dataset):
        if dataset not in self.loaded:
            snap = store.read(self.endpoint, dataset, refresh_interval=1800)
            self.loaded[dataset] = snap
            self.data.update({k: v for k, v in snap["data"].items() if k not in {"catalogues", "masters"}})
            earliest = min(self.loaded.values(), key=lambda item: item["as_of"])
            self.snapshot.update({key: earliest[key] for key in ("as_of", "completed_at")})
            self.snapshot.update(generation=":".join(self.loaded[key]["generation"] for key in sorted(self.loaded)),
                                 stale=any(item["stale"] for item in self.loaded.values()))
        return self.loaded[dataset]

    def metadata(self):
        return {k: v for k, v in self.snapshot.items() if k != "data"}

    def masters(self):
        vehicles = self._load("vehicles")["data"]["masters"]
        company = self._load("company")["data"]["masters"]
        prefixes = self.list_prefixes()
        return {**copy.deepcopy(company), **copy.deepcopy(vehicles), "prefixes": prefixes,
                "_snapshot": {k: self.snapshot[k] for k in ("as_of", "generation", "stale")}}

    def paints(self, car_id):
        self._load("vehicles")
        if str(car_id) not in self.data["paints"]:
            raise DmsClientError("Car is absent from the complete snapshot", "ERR_DMS_MASTER_CHANGED")
        return copy.deepcopy(self.data["paints"][str(car_id)])

    def _customers(self):
        """`(编号 → 行, 是否完整)`：完整客户组优先；没有就用"索引 + 已核实写回"。"""
        if getattr(self, "index_rows", None) is not None:
            return self.index_rows, False
        try:
            self._load("customers")
            return self.data["customers"], True
        except store.SnapshotUnavailable as missing:
            try:
                self._load("customer_index")
            except store.SnapshotUnavailable:
                raise missing from None
        overlays = store.customer_overlays(self.endpoint)
        rows = {cid: dict(row) for cid, row in self.data["index"].items()}
        rows.update({cid: {key: str(fields.get(key) or "") for key in ("cuscode", "name", "people_id")}
                     for cid, fields in overlays.items()})
        self.index_rows, self.index_details = rows, overlays
        return self.index_rows, False

    def _detail(self, cid, complete):
        if complete:
            return self.data["customers"][cid]
        if cid in self.index_details:
            return self.index_details[cid]
        raise CustomerDetailPending(cid)

    def list_prefixes(self):
        self._customers()
        if not isinstance(self.data.get("prefixes"), list) or not self.data["prefixes"]:
            raise store.SnapshotUnavailable("ERR_DMS_SNAPSHOT_PENDING")
        return copy.deepcopy(self.data["prefixes"])

    def list_geo(self, level, parent_id=""):
        if level not in ("provinces", "districts", "subdistricts", "zipcodes"):
            raise DmsClientError("Unknown address level", "ERR_DMS_MASTER_UNMATCHED")
        parent = "" if level == "provinces" else str(parent_id or "")
        if self.geo is None:
            snapshot = store.read(self.endpoint, "geo", refresh_interval=86400)
            self.geo = snapshot["data"]
            self.snapshot.update(geo_as_of=snapshot["as_of"], geo_stale=snapshot["stale"],
                                 geo_generation=snapshot["generation"])
        return copy.deepcopy(self.geo[level].get(parent, []))

    def resolve_address(self, address):
        return resolve(address, self.list_geo("provinces"), self.list_geo)

    def read_customer(self, customer_id):
        rows, complete = self._customers()
        if str(customer_id) not in rows:
            # The caller selected a specific customer, not a new-customer search.
            raise DmsClientError("Selected customer is absent from snapshot", "ERR_DMS_CUSTOMER_LOOKUP")
        return copy.deepcopy(self._detail(str(customer_id), complete))

    def lookup_customer(self, people_id, *, fallback_customer_ids=None):
        rows, complete = self._customers()
        target = normalize_thai_id(people_id)
        if not re.fullmatch(r"[0-9]{13}", target):
            raise DmsClientError("Full customer identity required", "ERR_DMS_CUSTOMER_LOOKUP")
        # A recent native ledger hint is not proof of identity, but ignoring a
        # customer absent from this generation can falsely offer "new customer"
        # after an optional write-through failure or initial cache bootstrap.
        # Wait for the next complete scan; never restore foreground live reads.
        if any(str(cid) not in rows for cid in (fallback_customer_ids or []) if cid):
            raise store.SnapshotUnavailable("ERR_DMS_SNAPSHOT_PENDING")
        matches = [cid for cid, row in rows.items()
                   if normalize_thai_id(row.get("people_id", "")) == target]
        if len(matches) > 1:
            raise DmsClientError("Ambiguous customer identity", "ERR_DMS_CUSTOMER_LOOKUP")
        if not matches:
            return {"found": False, "customer_id": None, "fields": {}}
        cid = matches[0]
        return {"found": True, "customer_id": cid, "fields": copy.deepcopy(self._detail(cid, complete))}

    def search_customers_detailed(self, text, limit=10):
        rows, _complete = self._customers()
        needle = str(text or "").strip().casefold()
        if not needle:
            return []
        matches = []
        for cid, row in rows.items():
            if any(needle in str(row.get(key, "")).casefold() for key in ("name", "people_id", "cuscode")):
                matches.append({"customer_id": cid, **{k: str(row.get(k, "")) for k in ("name", "people_id", "cuscode")}})
        return matches[:max(1, min(int(limit), 100))]
