"""Compare menu-5 output with native selectors and an independent HTML parser.

Read-only test account queries. Never advances an order to create dashboard data.
"""
import json
import os
from pathlib import Path

import frappe
from bs4 import BeautifulSoup

os.chdir('/home/frappe/frappe-bench/sites')
SITE = 'dms-line-lab.localhost'
frappe.init(site=SITE, sites_path='.')
frappe.connect()
frappe.set_user('Administrator')
from pearnly_dms.intake.service import run_logged_in
from pearnly_dms.ocr.ocr_service import resolve_dms_endpoint
from pearnly_dms.query.sales_readback import fetch_sales_records, fetch_top_sales, STATUS_FILTERS

labels = ['ทั้งหมด', 'ยกเลิก', 'ไม่ยกเลิก', 'แบบร่าง', 'ใบจอง', 'ซื้อเงินสด',
          'จัดไฟแนนซ์รออนุมัติ', 'จัดไฟแนนซ์อนุมัติ', 'รอ ผจก.ขายอนุมัติ',
          'อนุมัติพร้อมรับรถ', 'ยังไม่ได้จัดสรรโควต้า', 'จัดสรรโควต้าแล้วยังไม่เปิดสัญญา',
          'เปิดสัญญาแล้ว', 'ยังไม่เปิดสัญญาและยังไม่ยกเลิก', 'เกินวันที่คาดว่าจะส่งมอบรถ']
fields = ['cancel_status', 'record_status', 'booking_no', 'booking_date', 'delivery_date',
          'advisor', 'customer', 'vehicle', 'color', 'finance_status', 'allocation_status',
          'sales_doc_no', 'engine_no']
checks, native, charts = [], {}, {}
def check(label, value, **data):
    checks.append({'case': label, 'ok': bool(value), **data})
    print(json.dumps(checks[-1]), flush=True)
    assert value, label

try:
    for display in ('E2E DMS operator', 'E2E query manager'):
        user = frappe.db.get_value('DMS Operator', {'display_name': display}, 'user')
        def read(client, adapter):
            view = BeautifulSoup(client._post_text('drfcbc/view.php', {'idmenu': '25', 'menulv': '2'}), 'html.parser')
            selector = view.select_one('#selfilterdata')
            assert selector
            options = {x['value']: x.get_text(strip=True) for x in selector.select('option')}
            check(display + ' all native status IDs and meanings', options == {str(i+1): label for i, label in enumerate(labels)})
            result = {}
            for status, code in STATUS_FILTERS.items():
                body = client._post_text('drfcbc/component/showdata.php', {'sdtamt': '30', 'sdtpage': '1',
                    'sd': '', 'ftd': code, 'selcolsort': '1', 'selcolsorttype': '2'})
                soup = BeautifulSoup(body, 'html.parser')
                rows = []
                for item in soup.select('[data-val]'):
                    # Native rows append an audit popover, outside the 13 list
                    # columns. Exclude that separately identified component.
                    cells = [x.get_text(' ', strip=True) for x in item.select('p') if not x.find_parent(class_='statuscf')]
                    assert len(cells) == len(fields), 'Native row layout changed'
                    rows.append({'id': item['data-val'], **dict(zip(fields, ['' if x == '-' else ' '.join(x.split()) for x in cells]))})
                assert rows or body.strip().startswith('ndt::'), 'Invalid native empty response'
                result[status] = rows
            return {'ok': True, 'rows': result}
        direct = run_logged_in(resolve_dms_endpoint(user, None), read)
        assert direct.get('ok')
        native[display] = direct['rows']
        for status in STATUS_FILTERS:
            actual = fetch_sales_records(user, status=status, limit=30)
            check(display + ' ' + status + ' matches native rows and fields',
                  actual.get('ok') and actual['rows'] == direct['rows'][status], rows=len(actual.get('rows') or []))
    for group in ('model', 'type', 'subtype'):
        for metric in ('quantity', 'amount'):
            result = fetch_top_sales(user, group=group, metric=metric, date_from='01/01/2543', date_to='20/09/2569')
            charts[group + '/' + metric] = result
            check('historical ranking ' + group + '/' + metric, result.get('ok'), rows=len(result.get('rows') or []))
finally:
    output = Path(SITE + '/private/line-lab-query-semantics.json')
    output.write_text(json.dumps({'checks': checks, 'native': native, 'charts': charts, 'tested_at': frappe.utils.now()}, ensure_ascii=False, indent=2))
    output.chmod(0o600)
    frappe.destroy()
