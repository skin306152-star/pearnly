"""单据打印的两处越权（验收 10-26）：

- 打印只认库里已有、当前用户读得到的单：原生打印接口收得下页面伪造的整份单据 JSON，不重取就能把别家公司的供应商税号印出来；
- 公司 Logo 只认图片，且是挂在这家公司 Logo 上的，或本人刚上传、没挂到别处的；别人的、挂在别的单据上的私有文件拒收。

Run with Frappe installed. No Site, Redis or network: database reads are replaced.
"""
import types
import unittest
from unittest import mock


class Refused(Exception):
    pass


class PrintOnlyStoredReadableDocuments(unittest.TestCase):
    def setUp(self):
        from pearnly_erp_portal.printing import context

        for name, value in (("throw", lambda message, exc=Exception: (_ for _ in ()).throw(exc(message))),
                            ("_", lambda text: text)):
            patcher = mock.patch.object(context.frappe, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_a_forged_or_unreadable_document_is_refused(self):
        import frappe

        from pearnly_erp_portal.printing import context

        forged = types.SimpleNamespace(doctype=context.PURCHASE, name="PUR-FAKE")
        with mock.patch.object(context.frappe, "db", types.SimpleNamespace(exists=lambda doctype, name: False)):
            with self.assertRaises(frappe.PermissionError):
                context.pearnly_print_context(forged)
        stored = types.SimpleNamespace(doctype=context.PURCHASE, name="PUR-1", has_permission=lambda ptype: False)
        with mock.patch.object(context.frappe, "db", types.SimpleNamespace(exists=lambda doctype, name: True)), \
                mock.patch.object(context.frappe, "get_doc", lambda doctype, name: stored):
            with self.assertRaises(frappe.PermissionError):
                context.pearnly_print_context(types.SimpleNamespace(doctype=context.PURCHASE, name="PUR-1"))
        with self.assertRaises(frappe.PermissionError):
            context.pearnly_print_context(types.SimpleNamespace(doctype="ERP Sale", name="SAL-1"))

    def test_the_stored_copy_is_what_gets_printed(self):
        from pearnly_erp_portal.printing import context

        stored = types.SimpleNamespace(doctype=context.STOCK, name="STE-1", has_permission=lambda ptype: True)
        with mock.patch.object(context.frappe, "db", types.SimpleNamespace(exists=lambda doctype, name: True)), \
                mock.patch.object(context.frappe, "get_doc", lambda doctype, name: stored), \
                mock.patch.object(context, "_stock", lambda doc: {"printed": doc}):
            self.assertIs(context.pearnly_print_context(types.SimpleNamespace(doctype=context.STOCK, name="STE-1",
                                                                              warehouse="forged"))["printed"], stored)


class CompanyLogoIsOwnedByTheCompany(unittest.TestCase):
    def logo(self, rows, user="u@example.com"):
        from pearnly_erp_portal.masters import company

        attached = []
        doc = types.SimpleNamespace(name="CO-1")
        files = [frappe_dict(row) for row in rows]
        with mock.patch.object(company.frappe, "get_all", lambda doctype, **kwargs: files), \
                mock.patch.object(company.frappe, "db", types.SimpleNamespace(set_value=lambda doctype, name, values: attached.append((name, values)))), \
                mock.patch.object(company.frappe, "session", types.SimpleNamespace(user=user)), \
                mock.patch.object(company, "_", lambda text: text), \
                mock.patch.object(company.frappe, "throw", side_effect=lambda message, *a: (_ for _ in ()).throw(Refused(message))):
            company._attach_logo(doc, "/private/files/logo.png")
        return attached

    def test_only_an_own_fresh_image_or_this_companys_logo_is_accepted(self):
        mine = {"name": "F1", "owner": "u@example.com", "file_name": "logo.png", "is_folder": 0,
                "attached_to_doctype": None, "attached_to_name": None}
        self.assertEqual(self.logo([mine]), [("F1", {"attached_to_doctype": "ERP Company", "attached_to_name": "CO-1",
                                                      "attached_to_field": "logo"})])
        self.assertEqual(self.logo([{**mine, "owner": "x@example.com", "attached_to_doctype": "ERP Company",
                                     "attached_to_name": "CO-1"}]), [])  # 已经挂在这家公司上：照用，不再改挂
        for bad in ({**mine, "owner": "x@example.com"},  # 别人传的
                    {**mine, "attached_to_doctype": "Purchase Document", "attached_to_name": "PUR-9"},  # 挂在别的单据上
                    {**mine, "attached_to_doctype": "ERP Company", "attached_to_name": "CO-2"},  # 别家公司的 Logo
                    {**mine, "file_name": "secret.pdf"}):  # 不是图片
            with self.assertRaises(Refused):
                self.logo([bad])
        with self.assertRaises(Refused):
            self.logo([])

    def test_printing_reads_only_the_file_attached_to_this_company(self):
        from pearnly_erp_portal.printing import context

        asked = []
        with mock.patch.object(context.frappe, "db", types.SimpleNamespace(get_value=lambda doctype, filters, field: asked.append(filters))),\
                mock.patch.object(context.frappe, "get_app_path", lambda *parts: __file__):
            context.logo({"logo": "/private/files/acc-secret.png"}, "CO-1")
        self.assertEqual(asked, [{"file_url": "/private/files/acc-secret.png", "attached_to_doctype": "ERP Company",
                                  "attached_to_name": "CO-1"}])


def frappe_dict(row):
    import frappe

    return frappe._dict(row)


if __name__ == "__main__":
    unittest.main()
