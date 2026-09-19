"""Compatibility entry points for published RPCs or queued jobs. No business logic."""

from pearnly_dms.query.query_flow import (
    _run_records as _run_records,
    _run_top as _run_top,
)
