from frappe.model.document import Document
from pearnly_cowork.ocr.execution import store


class CoworkOCRBatch(Document):
    def before_insert(self):
        store.validate(self)

    def before_validate(self):
        store.validate(self)

    def autoname(self):
        self.name = store.digest(["cowork.invoice", self.source_file])

    def validate(self):
        store.validate(self)

    def on_trash(self):
        store.reject_delete(self)
