"""Bounded native connection inputs; no framework, clock, or IO."""
from copy import deepcopy
import hashlib
import ipaddress
import json
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, uuid5

CONFIG_KEYS = frozenset((
    "system_url", "comidyear", "seldb", "account_set", "account_dir", "express_root",
    "account_company", "account_set_row", "revenue_acc", "ar_acc", "vat_output_acc",
    "fallback_acc", "ap_acc", "vat_input_acc", "directions", "autonomy", "default_doctype",
    "posting_profile", "stock_acccod", "seed_customer_code", "seed_product_code", "generic_product_code",
))
REPORTED_KEYS = frozenset(("reported_accounts", "reported_products", "reported_customers",
    "reported_stock_acc_groups", "catalog_fingerprint", "max_payload_version", "companion_version"))
SECRET_FIELDS = ("credential_username", "credential_password", "credential_token")


class ConnectionError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def parse(value, default=None):
    return json.loads(value) if isinstance(value, str) else (deepcopy(value) if value is not None else default)


def fingerprint(value):
    return hashlib.sha256(dumps(value).encode()).hexdigest()


def request_identity(site, actor, purpose, key):
    if not isinstance(key, str) or not key.strip() or len(key) > 200:
        raise ConnectionError("connection.request_key_invalid")
    return str(uuid5(NAMESPACE_URL, dumps([site, actor, purpose, key])))


def boolean(value):
    if value in (True, 1, "1"):
        return 1
    if value in (False, 0, "0"):
        return 0
    raise ConnectionError("connection.config_invalid")


def configuration(adapter, value):
    if adapter not in {"express", "mrerp"}:
        raise ConnectionError("connection.adapter_invalid")
    value = parse(value, {})
    if not isinstance(value, dict) or set(value) - CONFIG_KEYS or len(dumps(value).encode()) > 65536:
        raise ConnectionError("connection.config_invalid")
    # Credentials, collector facts and binding identity have separate writers.
    result = deepcopy(value)
    for key, item in result.items():
        if key == "posting_profile":
            if (not isinstance(item, dict) or set(item) - {"posting_mode", "inventory_usage"}
                    or item.get("posting_mode") not in {"non_stock", "direct_account", "stock", "manual_review"}
                    or item.get("inventory_usage", "unknown") not in {"none", "perpetual", "mixed", "unknown"}):
                raise ConnectionError("connection.config_invalid")
        elif key == "directions":
            entries = item.split(",") if isinstance(item, str) else item
            if not isinstance(entries, list) or any(not isinstance(v, str) for v in entries):
                raise ConnectionError("connection.config_invalid")
            if {v.strip().lower() for v in entries if v.strip()} - {"purchase", "sales"}:
                raise ConnectionError("connection.config_invalid")
        elif not isinstance(item, (str, int, bool, type(None))):
            raise ConnectionError("connection.config_invalid")
        if isinstance(item, str) and len(item) > 2000:
            raise ConnectionError("connection.config_invalid")
    if result.get("autonomy") not in (None, "", "manual", "standard", "auto"):
        raise ConnectionError("connection.config_invalid")
    if adapter == "mrerp":
        result.setdefault("system_url", "https://www.mrerp4sme.com")
    if result.get("system_url"):
        validate_url(result["system_url"])
    return result


def validate_url(value):
    """Storage syntax guard. Outbound adapters must separately pin public DNS."""
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower().rstrip(".")
        port = parsed.port
    except (TypeError, ValueError):
        raise ConnectionError("connection.url_invalid") from None
    if (parsed.scheme not in {"http", "https"} or not host or parsed.username or parsed.password
            or parsed.fragment or host in {"localhost", "metadata", "metadata.google.internal"}
            or host.endswith((".localhost", ".local", ".internal")) or "." not in host):
        raise ConnectionError("connection.url_invalid")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address and not address.is_global:
        raise ConnectionError("connection.url_invalid")
    return value


def preparation(value):
    """Only a trusted collector can attest complete source-context collection."""
    if value is None:
        return None
    value = parse(value)
    required = {"complete", "account_set", "root_key", "mappings", "supplier_profiles", "bank_index", "reported_config"}
    if not isinstance(value, dict) or set(value) != required or type(value["complete"]) is not bool:
        raise ConnectionError("connection.preparation_invalid")
    if (not isinstance(value["account_set"], str) or not value["account_set"].strip()
            or not isinstance(value["root_key"], (str, type(None)))
            or not isinstance(value["mappings"], dict) or not isinstance(value["supplier_profiles"], dict)
            or not isinstance(value["bank_index"], list) or not isinstance(value["reported_config"], dict)
            or set(value["reported_config"]) - REPORTED_KEYS or len(dumps(value).encode()) > 8 * 1024 * 1024):
        raise ConnectionError("connection.preparation_invalid")
    if (set(value["mappings"]) != {"accounts", "clients"}
            or any(not isinstance(value["mappings"][k], list) for k in ("accounts", "clients"))
            or any(not isinstance(row, dict) for k in ("accounts", "clients") for row in value["mappings"][k])):
        raise ConnectionError("connection.preparation_invalid")
    return deepcopy(value)
