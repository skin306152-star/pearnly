"""Optional per-run invoice pattern memory from ec2e5c957e6b; sequential and pure."""
import re


class InvoicePatternMemory:
    _PREFIX_RE = re.compile(r"^([A-Za-z]+)(\d{0,4})")

    def __init__(self, *, min_instances: int = 2):
        self.min_instances = min_instances
        self._patterns = {}
        self._instance_counts = {}

    @classmethod
    def _extract_pattern(cls, invoice_number):
        if not invoice_number:
            return None
        match = cls._PREFIX_RE.match(invoice_number)
        if not match or not match.group(1):
            return None
        return (match.group(1) + match.group(2)).upper()

    def record(self, seller_tax, invoice_number):
        if not seller_tax or not invoice_number:
            return
        pattern = self._extract_pattern(invoice_number)
        if not pattern:
            return
        self._patterns.setdefault(seller_tax, set()).add(pattern)
        self._instance_counts[seller_tax] = self._instance_counts.get(seller_tax, 0) + 1

    def check_anomaly(self, seller_tax, invoice_number):
        if not seller_tax or not invoice_number:
            return None
        new_pattern = self._extract_pattern(invoice_number)
        if not new_pattern:
            return None
        known = self._patterns.get(seller_tax)
        count = self._instance_counts.get(seller_tax, 0)
        if not known or count < self.min_instances or new_pattern in known:
            return None
        return (f"invoice_number {invoice_number!r} pattern={new_pattern!r} "
                f"differs from known patterns {sorted(known)} for seller_tax "
                f"{seller_tax} ({count} prior invoices)")
