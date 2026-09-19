"""Compatibility entry points for published RPCs or queued jobs. No business logic."""

from pearnly_dms.approval.approval_flow import (
    _execute_approved as _execute_approved,
)
