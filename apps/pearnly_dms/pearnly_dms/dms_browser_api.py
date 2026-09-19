"""Compatibility entry points for published RPCs or queued jobs. No business logic."""

from pearnly_dms.editor.browser_api import (
    browser_auth as browser_auth,
    browser_config as browser_config,
    browser_draft as browser_draft,
    browser_geo as browser_geo,
    browser_paints as browser_paints,
    browser_save as browser_save,
    browser_text as browser_text,
)
