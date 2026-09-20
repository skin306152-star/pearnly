"""Read existing synthetic order 21 and compare its native master-derived fields.

No new orders, uploads, account changes, or master writes. Evidence uses native
master forms and the advisor's native organization response, not the mapper's
generated outgoing payload. Empty source-account slots must remain empty.
"""
import json
import os
from decimal import Decimal
from pathlib import Path

from bs4 import BeautifulSoup
import frappe

os.chdir('/home/frappe/frappe-bench/sites')
SITE = 'dms-line-lab.localhost'
frappe.init(site=SITE, sites_path='.')
frappe.connect()
frappe.set_user('Administrator')


def fields(markup):
    result = {}
    for tag in BeautifulSoup(markup, 'html.parser').select('input[name],select[name],textarea[name]'):
        if tag.get('type') in ('checkbox', 'radio') and not tag.has_attr('checked'):
            continue
        if tag.name == 'select':
            option = tag.select_one('option[selected]') or tag.select_one('option')
            value = option.get('value', '') if option else ''
        else:
            value = tag.get_text() if tag.name == 'textarea' else tag.get('value', '')
        result[tag['name']] = str(value)
    return result


try:
    from pearnly_dms.intake.service import run_logged_in
    from pearnly_dms.ocr.ocr_service import resolve_dms_endpoint
    private = Path(SITE) / 'private'
    qa = json.loads((private / 'line-lab-fields-expected.json').read_text())
    operator = frappe.get_doc('DMS Operator', {'display_name': 'E2E DMS operator'})
    log = frappe.get_doc('Pearnly Push Log', {'actor': operator.user, 'business_key': 'BK000002609000021'})
    assert json.loads(log.request_body)['qa']['regis_name'] == 'E2E 20260920 all-fields no payment'
    response = json.loads(log.response_body)
    checks = {}

    def compare(key, expected, actual, money=False):
        ok = (Decimal(actual.replace(',', '') or '0') == Decimal(expected.replace(',', '') or '0')
              if money else actual == expected)
        checks[key] = {'expected': expected, 'actual': actual, 'match': ok}

    def read(client, adapter):
        order = fields(client._post_text('drfcbc/form.php', {'status': 'e', 'id': response['booking_id']}))
        car = fields(client._post_text('car/form.php', {'status': 'v', 'id': order['carval']}))
        vehicle = ['carbrandval','txtcarbrand','typecarval','txttypecar','typecardescval','txttypecardesc',
                   'gradeval','txtgrade','cargearval','txtcargear','txtmanuyear','enginepowerval','txtenginepower','txtprice']
        for key in vehicle:
            assert key in car and key in order, key
            compare(key, car[key], order[key], money=key=='txtprice')
        organization = json.loads(client._post_text('drfcbc/component/detailbooksell.php', {'idusers': order['usersval']}))
        assert len(organization) >= 12
        for side in ('book','sell'):
            names = [f'branch_{side}val',f'txtbranch_{side}',f'team_{side}val',f'txtteam_{side}']
            for level in (2,3,4,5):
                names.extend([f'usersposival{level}_{side}',f'txtusersposi{level}_{side}'])
            for key,value in zip(names,organization):
                assert key in order, key
                compare(key, '' if value in (None, '0', 'null', 'undefined') else str(value).strip(), order[key])
        transfer = next(p['extra'] for p in qa['payments'] if p['channel']=='transfer')
        for source,target in [('src_account_name','txtowneraccnametffrom'),('src_account_no','txtaccountnumtffrom'),
                              ('src_bank_name','txtbanknametffrom'),('src_bank_id','banktffromval'),('src_branch_name','txtbranchnametffrom')]:
            assert target in order, target
            compare(target,str(transfer.get(source) or ''),order[target])
        return {'ok': all(v['match'] for v in checks.values()), 'checks': checks}

    result = run_logged_in(resolve_dms_endpoint(operator.user,None),read,authoritative_read=True)
    path = private / 'line-lab-derived-readback.json'
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2));path.chmod(0o600)
    print(json.dumps({'ok':result.get('ok'),'checked':len(checks),
                      'matched':sum(x['match'] for x in checks.values()),
                      'mismatched':[k for k,v in checks.items() if not v['match']],
                      'error_code':result.get('error_code'),'external_writes':0}))
    assert result.get('ok') and len(checks)==43
finally:
    frappe.destroy()
