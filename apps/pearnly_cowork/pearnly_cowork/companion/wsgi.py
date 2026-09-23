"""Explicit optional entrypoint; existing deployments still use frappe.app.

Selecting this application only enables the fixed old transport URLs. It does
not provision a tenant, issue/import a credential, or switch any Windows profile.
"""
from frappe.app import application as native_application

from .legacy_wire import LegacyAgentWire

application = LegacyAgentWire(native_application)
