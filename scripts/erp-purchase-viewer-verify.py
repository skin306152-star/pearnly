"""Dedicated purchase lab, read-only preview checks. No external or paid IO.

Uses the retained UI receipt; malformed/multipage bytes are in-memory substitutes.
Writes only regenerable PDF/PNG upload fixtures under /tmp/erp-purchase-viewer.
"""
import base64
import os
from pathlib import Path
from unittest.mock import patch

import frappe
import pymupdf


def main():
    root = Path('/home/frappe/frappe-bench/sites')
    assert (root / '.erp-pos-lab').exists()
    os.chdir(root)
    frappe.init(site='erp-pos-a.localhost')
    frappe.connect()
    assert frappe.conf.pearnly_erp_pos_lab and frappe.conf.db_host == 'db'
    from pearnly_erp_portal.purchasing.preview import page_image
    from frappe.core.doctype.file.file import File

    file = frappe.db.get_value('File', {'attached_to_name': 'PUR-2026-00198',
                                      'file_name': 'PUR-2026-00198-substitute_receipt.pdf'}, 'name')
    assert file
    frappe.set_user('purchase-preview@example.test')
    result = page_image(file)
    assert result['pages'] == 1 and base64.b64decode(result['image'].split(',')[1]).startswith(b'\x89PNG')

    def rejects(callback):
        try:
            callback()
        except (frappe.ValidationError, frappe.PermissionError, frappe.AuthenticationError):
            return
        raise AssertionError('Expected rejection')

    rejects(lambda: page_image(file, 0))
    rejects(lambda: page_image(file, 2))
    rejects(lambda: page_image(file, 'invalid'))
    for content in (b'not a PDF', b'%PDF-invalid', b'%PDF-' + b'x' * (20 * 1024 * 1024)):
        with patch.object(File, 'get_content', return_value=content):
            rejects(lambda: page_image(file))

    with pymupdf.open() as document:
        for number in (1, 2):
            page = document.new_page(width=400, height=560)
            page.insert_text((35, 60), f'PURCHASE VIEWER TEST - PAGE {number}', fontsize=16)
            page.insert_text((35, 100), 'Fixture invoice / THB 214.00 / Synthetic data', fontsize=12)
        content = document.tobytes()
        folder = Path('/tmp/erp-purchase-viewer')
        folder.mkdir(exist_ok=True)
        (folder / 'purchase-viewer-test.pdf').write_bytes(content)
        document[0].get_pixmap().save(str(folder / 'purchase-viewer-test.png'))
    with patch.object(File, 'get_content', return_value=content):
        first, second = page_image(file, 1), page_image(file, 2)
        assert first['pages'] == second['pages'] == 2 and first['image'] != second['image']
    frappe.set_user('purchase-other@example.test')
    rejects(lambda: page_image(file))
    frappe.set_user('Guest')
    rejects(lambda: page_image(file))
    frappe.db.rollback()
    frappe.destroy()
    print('PASS: actual private PDF; multipage render; invalid pages/content/size; other owner and Guest denied. No business writes.')


if __name__ == '__main__':
    main()
