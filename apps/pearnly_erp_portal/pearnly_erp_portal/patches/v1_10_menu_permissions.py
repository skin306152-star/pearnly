"""Split existing employee roles into equivalent explicit menu grants once."""
def execute():
    from pearnly_erp_portal.access.setup import migrate_members

    migrate_members()
