from frappe.model.document import Document
from pearnly_cowork.files.guard import validate_source, deny_delete


class CoworkSourceFile(Document):
    def before_insert(self):
        validate_source(self)

    def before_validate(self):
        validate_source(self)

    def autoname(self):
        self.name = "CWS-" + self.request_key[:48]

    def validate(self):
        validate_source(self)

    def on_trash(self):
        deny_delete(self)
