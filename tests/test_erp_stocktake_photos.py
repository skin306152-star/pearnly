"""盘点照片（验收 10-26）：手机盘点页发的是 JPEG 的 base64（固定以「/9j/」开头），不能按首字符「/」当成文件地址；
带 data: 头的也认；本站文件地址只认 /files/… 与 /private/files/… 的样子。照片在拿盘点锁之前存好（原件层是网络上传）。

Run with Frappe installed. No Site, Redis or network: database reads are replaced.
"""
import base64
import types
import unittest
from unittest import mock

JPEG = base64.b64encode(b"\xff\xd8\xff\xe0" + b"\x00" * 64).decode()


class StocktakePhotos(unittest.TestCase):
    def store(self, values, url_owner="u@example.com"):
        from pearnly_core.originals import registered

        from pearnly_erp_portal.inventory import count

        put, looked_up = [], []

        class File(dict):
            def insert(self, ignore_permissions=False):
                return types.SimpleNamespace(name="LOCAL-1")

        db = types.SimpleNamespace(get_value=lambda doctype, filters, field, order_by=None: (
            looked_up.append(filters) or ("FILE-URL" if filters["owner"] == url_owner else None)))
        with mock.patch.object(registered, "put", lambda original_id, filename, raw, **fields: put.append(
                (filename, raw, fields)) or None), \
                mock.patch.object(count.frappe, "db", db), \
                mock.patch.object(count.frappe, "get_doc", lambda body: File(body)), \
                mock.patch.object(count.frappe, "local", types.SimpleNamespace(site="t.example")), \
                mock.patch.object(count.frappe, "session", types.SimpleNamespace(user="u@example.com")), \
                mock.patch.object(count, "_", lambda text: text), \
                mock.patch.object(count.frappe, "throw", side_effect=lambda message, *a: (_ for _ in ()).throw(ValueError(message))):
            names = count._store_photos(values)
        return names, put, looked_up

    def test_a_jpeg_payload_is_a_photo_not_a_file_address(self):
        self.assertTrue(JPEG.startswith("/9j/"))
        names, put, looked_up = self.store([JPEG, "data:image/jpeg;base64," + JPEG])
        self.assertEqual(names, ["LOCAL-1", "LOCAL-1"])  # 站点没配原件层：存本机私有文件
        self.assertEqual([raw[:3] for _name, raw, _fields in put], [b"\xff\xd8\xff"] * 2)
        self.assertEqual([fields for _name, _raw, fields in put], [{}, {}])  # 先不挂，拿锁后再挂到记录上
        self.assertEqual(looked_up, [])

    def test_only_an_own_site_file_address_is_taken_as_an_upload(self):
        names, put, looked_up = self.store(["/private/files/photo.jpg"])
        self.assertEqual((names, put), (["FILE-URL"], []))
        self.assertEqual(looked_up[0]["file_url"], "/private/files/photo.jpg")
        with self.assertRaises(ValueError):
            self.store(["/private/files/photo.jpg"], url_owner="someone-else")
        with self.assertRaises(ValueError):
            self.store(["/not/base64 at all"])

    def test_photos_are_stored_before_the_count_lock(self):
        import inspect

        from pearnly_erp_portal.inventory import count

        for function in (count.add_entry, count.edit_entry):
            source = inspect.getsource(function)
            self.assertLess(source.index("_store_photos("), source.index("count_state.lock_task("))


if __name__ == "__main__":
    unittest.main()
