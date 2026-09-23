"""Two real DB connections: an in-flight receipt must prevent stale count posting.

Dedicated ERP/POS rule lab only. Synthetic records are retained in its volume;
temporary rendezvous files are removed. No external calls.
"""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid

import frappe

spec = importlib.util.spec_from_file_location('fixtures', Path(__file__).with_name('erp-pos-lab-products-verify.py'))
fx = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fx)


def wait_file(path):
    deadline = time.monotonic() + 20
    while not path.exists():
        if time.monotonic() > deadline:
            raise AssertionError('Timed out waiting for transaction rendezvous')
        time.sleep(.05)


def worker(mode, root, payload):
    fx.connect()
    frappe.set_user(fx.OWNER)
    from pearnly_erp_portal.inventory import count, stock
    root = Path(root)
    if mode == 'receipt':
        stock.apply_movement(product=payload['product'], warehouse=payload['warehouse'],
                             location='A', movement_type='stock_in', qty=1, unit_cost=7)
        (root/'receipt-locked').touch()
        wait_file(root/'release')
        frappe.db.commit()
    else:
        (root/'finish-started').touch()
        try:
            count.close_count(payload['task'])
        except frappe.ValidationError:
            frappe.db.rollback()
            (root/'finish-rejected').touch()
        else:
            frappe.db.rollback()
            raise AssertionError('Concurrent receipt was overwritten')
    frappe.destroy()


def main():
    fx.connect()
    frappe.set_user(fx.OWNER)
    from pearnly_erp_portal.inventory import count, stock, warehouses
    from pearnly_erp_portal.products import service
    key = 'WB-CONCURRENT-' + uuid.uuid4().hex[:8]
    product = service.create_product(product_name=key, unit='件')['name']
    wh = warehouses.resolve(key, create=True)
    stock.apply_movement(product=product, warehouse=wh, location='A', movement_type='stock_in', qty=5, unit_cost=7)
    task = count.create_count(title=key, warehouse=wh)['name']
    row = count.list_items(count=task)[0]
    count.add_entry(count=task, item=row['name'], qty=4)
    frappe.db.commit()
    payload = json.dumps({'product': product, 'warehouse': wh, 'task': task})
    with tempfile.TemporaryDirectory(prefix='stocktake-concurrency-') as directory:
        root = Path(directory)
        processes = []
        try:
            receipt = subprocess.Popen([sys.executable, __file__, 'receipt', directory, payload])
            processes.append(receipt)
            wait_file(root/'receipt-locked')
            finish = subprocess.Popen([sys.executable, __file__, 'finish', directory, payload])
            processes.append(finish)
            wait_file(root/'finish-started')
            time.sleep(.5)
            assert finish.poll() is None, 'Finishing must wait for the product lock'
            (root/'release').touch()
            for process in processes:
                assert process.wait(timeout=20) == 0
            assert (root/'finish-rejected').exists()
        finally:
            (root/'release').touch()
            for process in processes:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=5)
    frappe.db.rollback()
    assert stock.current_balance(product, wh, 'A').qty == 6
    assert frappe.db.get_value('ERP Stock Count', task, 'status') == 'Active'
    assert not frappe.db.count('ERP Stock Movement', {'ref_type': 'count', 'ref_id': task})
    print(json.dumps({'ok': True, 'task': task, 'check': 'receipt lock -> stale finish rejected -> receipt retained'}))
    frappe.destroy()


if __name__ == '__main__':
    if len(sys.argv) > 1:
        worker(sys.argv[1], sys.argv[2], json.loads(sys.argv[3]))
    else:
        main()
