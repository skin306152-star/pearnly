from frappe.model.document import Document
from pearnly_cowork.history.storage import validate_document


class CoworkOCRHistory(Document):
    def autoname(self):
        self.name = "CWH-" + self.request_key[:48]

    def validate(self):
        validate_document(self)
