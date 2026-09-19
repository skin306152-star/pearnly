"""Compatibility entry points for published RPCs or queued jobs. No business logic."""

from pearnly_dms.booking.conversation.booking_flow import (
    _execute_booking as _execute_booking,
)
