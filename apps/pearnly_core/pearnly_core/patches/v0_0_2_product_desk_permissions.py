"""Revoke framework role/permission mutation from product-admin custom grants."""
from pearnly_core.desk.permissions import restrict_platform_permissions


def execute():
    restrict_platform_permissions()
