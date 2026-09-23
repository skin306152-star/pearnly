from frappe.model.document import Document
from pearnly_cowork.masters import workspaces


class CoworkWorkspace(Document):
    def validate(self):
        workspaces.validate(self)
