"""Compatibility entry points for published RPCs or queued jobs. No business logic."""

from pearnly_dms.operators.roster import (
    api_create_operator as api_create_operator,
    api_delete_operator as api_delete_operator,
    api_list_operators as api_list_operators,
    api_operator_bind_code as api_operator_bind_code,
    api_set_operator_status as api_set_operator_status,
    api_update_operator as api_update_operator,
)
