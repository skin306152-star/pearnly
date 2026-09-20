"""Upgrade existing rosters without contacting the external DMS."""
from pearnly_dms.operators.endpoints import backfill


def execute():
    backfill()
