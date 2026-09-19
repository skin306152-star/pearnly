"""Compatibility entry points for published RPCs or queued jobs. No business logic."""

from pearnly_dms.intake.id_card import (
    api_confirm_intake as api_confirm_intake,
    api_create_intake as api_create_intake,
    api_list_intake as api_list_intake,
    api_match_customer as api_match_customer,
    api_queue_push as api_queue_push,
    api_summary as api_summary,
)
