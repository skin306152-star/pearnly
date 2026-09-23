"""Native entry-point guard, shared only by these three Cowork DocTypes."""
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
        # Frappe User Permission defaults must not rewrite explicit no-client.
        self.client = self.client or ""

    def validate(self):
        access.guard(self)

    def on_trash(self):
        access.deny()

    def before_rename(self, *args, **kwargs):
        access.deny()
