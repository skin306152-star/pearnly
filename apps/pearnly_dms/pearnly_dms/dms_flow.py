"""Compatibility entry points for published RPCs or queued jobs. No business logic."""

from pearnly_dms.intake.flow import (
    process_image as process_image,
    run_dedup as run_dedup,
    write_create as write_create,
    write_update as write_update,
)
