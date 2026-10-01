"""补上台账的查询索引（2026-10-01 生产计费库缺失，见 payments.usage.ensure_indexes）。幂等。"""
from pearnly_control.payments.usage import ensure_indexes


def execute():
    ensure_indexes()
