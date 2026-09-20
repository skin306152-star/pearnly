"""Capture the owned editor fixture, then independently read its external result.

Only --capture writes local evidence. Neither mode submits any external order.
The assertions describe the explicit browser inputs, not a replay of the adapter.
"""
import argparse
import json
import os
from decimal import Decimal
from pathlib import Path

import frappe

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--capture', action='store_true')
args = parser.parse_args()
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dms-line-lab.localhost', sites_path='.')
frappe.connect()
frappe.set_user('Administrator')
path = Path('dms-line-lab.localhost/private/line-lab-fields-expected.json')
marker = 'E2E 20260920 all-fields no payment'
optional = ('building', 'floor', 'room', 'village', 'moo', 'soi', 'road')
customer_expected = {
    'name': 'E2E 20260920 field-matrix', 'people_id': '1000009200019',
    'birthday_be': '29/02/2531', 'phone': '0990000922', 'house_no': '920/2',
    **dict.fromkeys(optional, ''),
    'prefix_name': 'คุณ', 'province_name': 'ระยอง', 'district_name': 'เมืองระยอง',
    'subdistrict_name': 'ทับมา', 'zipcode_name': '21000',
}
try:
    if args.capture:
        from pearnly_core.line_session import get_session
        session = get_session('Ulab-alice')
        qa = session['payload']['qa']
        assert session['state'] == 'booking_review' and qa['customer']['id'] == '127'
        assert qa['answers']['regis_name'] == marker
        for key, value in customer_expected.items():
            assert str(qa['draft'].get('zipcode' if key == 'zipcode_name' else key) or '') == value, key
        assert len(qa['payments']) == 6
        assert not qa['files']['id_card_mid'] and qa['files']['slip_mid']
        path.write_text(json.dumps(qa, ensure_ascii=False, indent=2))
        path.chmod(0o600)
        print(json.dumps({'captured': True, 'fields': len(customer_expected), 'channels': 6}))
    else:
        from pearnly_dms.intake.service import run_logged_in
        from pearnly_dms.ocr.ocr_service import resolve_dms_endpoint
        qa = json.loads(path.read_text())
        operator = frappe.get_doc('DMS Operator', {'display_name': 'E2E DMS operator'})
        logs = frappe.get_all('Pearnly Push Log', filters={'actor': operator.user, 'status': 'success'},
                             fields=['name', 'business_key', 'request_body', 'response_body'])
        logs = [row for row in logs if json.loads(row.request_body or '{}').get('qa', {}).get('regis_name') == marker]
        assert len(logs) == 1, 'Exactly one successful fixture booking is required'
        log = logs[0]
        response = json.loads(log.response_body)
        answers = qa['answers']
        expected = {
            'cusval': '127', 'txtpeopleid': customer_expected['people_id'],
            'txtcus': customer_expected['name'], 'txtbirthday': '29/02/2531', 'txttel': '0990000922',
            'prefixval': qa['draft']['prefix_id'], 'txtprefix': 'คุณ',
            'txthousenum': '920/2', **{'txt' + key: '' for key in optional},
            'txtprovinces': 'ระยอง', 'txtdistricts': 'เมืองระยอง',
            'txtsubdistricts': 'ทับมา', 'txtzipcodes': '21000',
            'usersval': qa['advisor']['id'], 'txtusers': 'sale02',
            'placebookval': answers['place']['id'], 'txtplacebook': 'นอกสถานที่',
            'carval': '2', 'carpaintval': answers['paint']['id'], 'carpaintname': 'แดง',
            'termsaleval': answers['term']['id'], 'txttermsale': 'ซื้อเงินสด',
            'regisbehalfval': answers['regis']['id'], 'txtregisbehalf': 'บุคคลธรรมดา',
            'txtregisname': marker, 'txtcardeliverydate': '29/02/2571',
            'txtmoneycash': '.01', 'txtmoneytfmon': '.02', 'txtmoneycheque': '.03',
            'txtmoneycashiercq': '.04', 'txtmoneycddbc': '.05', 'txtmoneyother': '.06',
            'txtearnestmoney': '.21',
            'txtchequeno': 'E2E-CHEQUE-920', 'txtbooknocheque': 'E2E-BOOK-920',
            'txtbanknamecheque': 'E2E TEST BANK', 'bankchequeval': '',
            'txtcashiercqno': 'E2E-CASHIER-920', 'txtbooknocashiercq': 'E2E-CASHIER-BOOK',
            'txtbanknamecashiercq': 'E2E CASHIER BANK', 'bankcashiercqval': '',
            'txtbanknamecddbc': 'E2E CARD BANK', 'txttypenamecddbc': 'E2E TEST CARD', 'bankcddbcval': '',
            'txtdetailother': 'E2E all six channels - NO PAYMENT MADE',
        }
        transfer = next(p['extra'] for p in qa['payments'] if p['channel'] == 'transfer')
        for source, target in [('dst_bank_id', 'banktfmonval'), ('dst_bank_name', 'txtbanknametfmon'),
                               ('dst_account_no', 'txtaccountnumtfmon'), ('dst_branch_name', 'txtbranchnametfmon'),
                               ('dst_business_name', 'txtbusinessnametfmon')]:
            expected[target] = transfer.get(source, '')
        for source, target in [('province_id', 'provincesval'), ('district_id', 'districtsval'),
                               ('subdistrict_id', 'subdistrictsval'), ('zipcode_id', 'zipcodesval')]:
            expected[target] = qa['draft'][source]

        def read(client, adapter):
            form = client._parse_form_defaults(client._post_text('drfcbc/form.php', {
                'status': 'e', 'id': response['booking_id'],
            }))
            customer = client.read_customer('127')
            checks = {}
            for key, value in expected.items():
                actual = str(form.get(key, ''))
                equal = (Decimal(actual.replace(',', '') or '0') == Decimal(value)
                         if key.startswith('txtmoney') or key == 'txtearnestmoney' else actual == str(value))
                checks['booking.' + key] = {'expected': value, 'actual': actual, 'match': equal}
            for key, value in customer_expected.items():
                checks['customer.' + key] = {'expected': value, 'actual': customer.get(key),
                                              'match': customer.get(key) == value}
            for suffix in ('_ct', '_sd'):
                for key in ('house_no', *optional, 'province_id', 'district_id', 'subdistrict_id', 'zipcode_id'):
                    value = qa['draft'][key]
                    checks['customer.' + key + suffix] = {'match': str(customer.get(key + suffix, '')) == value}
            names = [value for key, value in form.items() if key.startswith('fulcurrname')]
            checks['id_card_removed'] = {'match': 'สำเนาบัตรประชาชน' not in names}
            checks['slip_present'] = {'match': 'ใบโอนเงินจอง' in names}
            checks['attachment_ledger'] = {'match': response.get('attach_ok') is True and response.get('attached') == 1}
            return {'ok': all(v['match'] for v in checks.values()), 'checks': checks,
                    'booking_no': log.business_key, 'booking_id': response['booking_id']}

        result = run_logged_in(resolve_dms_endpoint(operator.user, None), read, authoritative_read=True)
        output = path.with_name('line-lab-fields-readback.json')
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
        output.chmod(0o600)
        checks = result.get('checks', {})
        print(json.dumps({'ok': result.get('ok'), 'booking_no': result.get('booking_no'),
                          'checked': len(checks), 'matched': sum(v['match'] for v in checks.values()),
                          'mismatched': [key for key, value in checks.items() if not value['match']],
                          'error_code': result.get('error_code')}))
        if not result.get('ok'):
            raise SystemExit(1)
finally:
    frappe.destroy()
