from frappe.model.document import Document


class CoworkReconView(Document):
    """对账结果页的存档（``recon_center.view_store``）：只由服务端读写，没有任何角色的权限。"""
