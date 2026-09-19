"""Compatibility entry points for published RPCs or queued jobs. No business logic."""

from pearnly_dms.connections.api import (
    get_connection as get_connection,
    save_connection as save_connection,
    set_connection_enabled as set_connection_enabled,
    test_credentials as test_credentials,
    test_endpoint as test_endpoint,
)
