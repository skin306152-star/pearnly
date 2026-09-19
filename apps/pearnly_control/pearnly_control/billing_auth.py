"""Tenant/product-bound credentials. The root secret never leaves billing/control."""
import hashlib
import hmac

PRODUCTS = frozenset({"dms", "cowork", "erp"})


def tenant_token(root: str, tenant: str, product: str) -> str:
    if not root or not tenant or product not in PRODUCTS:
        raise ValueError("Invalid billing credential scope")
    payload = f"pearnly-billing-v1\0{tenant}\0{product}".encode()
    return hmac.new(root.encode(), payload, hashlib.sha256).hexdigest()


def valid_token(root: str, tenant: str, product: str, supplied: str) -> bool:
    if not root or not tenant or product not in PRODUCTS or not supplied:
        return False
    return hmac.compare_digest(tenant_token(root, tenant, product), supplied)
