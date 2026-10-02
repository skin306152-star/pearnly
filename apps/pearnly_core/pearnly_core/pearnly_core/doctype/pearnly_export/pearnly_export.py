"""One background export of one user (see ``pearnly_core.exports``); no customer-facing CRUD."""
from frappe.model.document import Document


class PearnlyExport(Document):
    pass
