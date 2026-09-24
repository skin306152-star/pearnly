"""Compatibility reads for old callers: published snapshots only.

The 45/90-second request-driven warmer is retired. Cache misses never log in or
wait for a worker; scheduling lives in snapshots.schedule.
"""
from pearnly_dms.snapshots.directory import Directory

SESSION_MAX_AGE_SECONDS = 300
BACKGROUND_MAX_AGE_SECONDS = 300
PAINT_MAX_AGE_SECONDS = 300


def get_session_masters(endpoint, *, max_age_seconds=300, require_complete=True, background=False):
    return Directory(endpoint).masters()


def get_session_paints(endpoint, car_id, *, max_age_seconds=300, require_complete=True, background=False):
    return Directory(endpoint).paints(car_id)
