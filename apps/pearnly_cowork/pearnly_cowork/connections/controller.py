"""Native entry-point guard, shared by the Cowork connection DocTypes (connection, refresh, snapshot) and the
multi-computer registry DocTypes (account set, reach, facts)."""
from frappe.model.document import Document
from . import access


class ConnectionDocument(Document):
    def autoname(self):
        access.guard(self)
        self.name = self.record_key

    def check_permission(self, permtype="read", permlevel=None):
        access.guard(self)

    def before_insert(self):
        access.guard(self)

    def before_validate(self):
        access.guard(self)
        # Frappe User Permission defaults must not rewrite explicit no-client. Only the DocTypes that have a client
        # field (connection, refresh, snapshot); the account-set registry, reach and facts share this guard without one
        # (2026-10-18: the first real-Site migration of the multi-computer patches failed on that missing attribute).
        if self.meta.has_field("client"):
            self.client = self.client or ""

    def validate(self):
        access.guard(self)

    def on_trash(self):
        access.deny()

    def before_rename(self, *args, **kwargs):
        access.deny()
