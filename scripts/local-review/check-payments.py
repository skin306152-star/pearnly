"""Native local billing regression. Rollback only; no OCR, SMTP or external IO.
Run inside the isolated preview container from its sites directory.
"""
import base64
from io import BytesIO
from unittest.mock import patch

import frappe
from PIL import Image
from werkzeug.test import EnvironBuilder
from werkzeug.wrappers import Request

frappe.init(site='control-preview.localhost');frappe.connect();frappe.set_user('Administrator')
assert frappe.local.site == 'control-preview.localhost'
from pearnly_control import billing, billing_api
from pearnly_control.billing_auth import tenant_token
from pearnly_control.payments.topup import evidence
image = BytesIO();Image.new('RGB',(8,8),'white').save(image,format='PNG')
proof={'content':base64.b64encode(image.getvalue()).decode(),'filename':'fixture.png'}
name='LOCAL-PAYMENT-CHECK'
assert not frappe.db.exists('Pearnly Tenant', name)
try:
    with patch.object(frappe.db,'commit'):
        frappe.get_doc({'doctype':'Pearnly Tenant','tenant_name':'Local payment check','status':'active'}).insert(ignore_permissions=True,set_name=name)
        frappe.local.request=Request(EnvironBuilder(method='POST',headers={
            'X-Pearnly-Billing-Product':'dms',
            'X-Pearnly-Billing-Token':tenant_token(frappe.conf.pearnly_billing_token,name,'dms')}).get_environ())
        result=billing.subscribe(name,'S')
        assert result['ok'] is False and result['error']=='insufficient_balance'
        assert not frappe.db.exists(billing.SUBSCRIPTION,{'tenant':name})
        assert billing.gate(name,kind='pdf',units=1,tier='A')['allowed'] is False
        before=frappe.db.count(billing.TOPUP)
        for bad in (None, {'content':'not-base64'}, {'content':base64.b64encode(b'<html>bad</html>').decode()}):
            try:billing_api.service_topup_request(name,100,proof=bad)
            except frappe.ValidationError:pass
            else:raise AssertionError('Invalid proof accepted')
        assert frappe.db.count(billing.TOPUP)==before
        first=billing_api.service_topup_request(name,200,payer_name='Fixture',note='Local check',proof=proof)
        assert first['status']=='pending' and billing.balance_of(name)==0
        with Image.open(BytesIO(base64.b64decode(evidence(first['request'])['content']))) as attached:
            assert attached.size == (8,8) and attached.convert('RGB').getpixel((0,0)) == (255,255,255)
        assert frappe.db.get_value('File',{'attached_to_doctype':billing.TOPUP,'attached_to_name':first['request']},'is_private')==1
        result=billing_api.admin_approve(first['request'],actual_amount_thb=175,note='Fixture received 175')
        assert result['new_balance']==175 and billing.balance_of(name)==175
        assert frappe.db.count(billing.TXN,{'tenant':name,'type':'topup'})==1
        try:billing_api.admin_approve(first['request'],actual_amount_thb=175)
        except frappe.ValidationError:pass
        else:raise AssertionError('Duplicate approval accepted')
        assert billing.balance_of(name)==175
        second=billing_api.service_topup_request(name,100,proof=proof)
        billing_api.admin_reject(second['request'],note='Fixture rejected')
        assert billing.balance_of(name)==175
        assert billing.subscribe(name,'S')['ok'] is True
        assert billing.balance_of(name)==25
        assert billing.gate(name,kind='pdf',units=1,tier='A')['allowed'] is True
        frappe.set_user('Guest')
        try:billing_api.admin_approve(second['request'],actual_amount_thb=100)
        except frappe.AuthenticationError:pass
        else:raise AssertionError('Guest review accepted')
    print('PASS native billing: zero balance refusal, no subscription, gated OCR, proof validation/private file, pending/no credit, actual amount, one ledger, duplicate/guest rejection, rejection/no credit, paid plan/quota')
finally:
    # Native File rollback hooks remove only files created by this transaction.
    frappe.db.rollback();frappe.destroy()

frappe.init(site='auth-preview.localhost');frappe.connect();frappe.set_user('preview@example.test')
try:
    from pearnly_core import billing_www, billing_client
    from pearnly_dms.ocr import ocr_service
    from pearnly_core.payments.proof import payload, upload
    for foreign in ('https://example.invalid/private/slip.png', '/private/files/not-owned.png'):
        try: payload(foreign)
        except frappe.ValidationError: pass
        else: raise AssertionError('Foreign evidence accepted')
    frappe.local.request = Request(EnvironBuilder(method='POST').get_environ())
    frappe.local.uploaded_file = image.getvalue()
    uploaded = upload()
    assert uploaded['doctype'] == 'File' and uploaded['is_private'] == 1
    assert base64.b64decode(payload(uploaded['file_url'])['content']) == image.getvalue()
    frappe.local.uploaded_file = None
    file = frappe.get_doc({'doctype':'File','file_name':'local-proof-regression.png',
        'content':image.getvalue(),'is_private':1,'attached_to_doctype':'User',
        'attached_to_name':'preview@example.test'}).insert(ignore_permissions=True)
    assert base64.b64decode(payload(file.file_url)['content']) == image.getvalue()
    frappe.set_user('Administrator')
    try: payload(file.file_url)
    except frappe.ValidationError: pass
    else: raise AssertionError('Other user evidence accepted')
    frappe.set_user('preview@example.test')
    assert billing_client.status('pdf',1,'A')['error_code']=='insufficient_balance'
    try:billing_www.subscribe('S')
    except frappe.ValidationError:pass
    else:raise AssertionError('Product endpoint masked rejected subscription')
    with patch.object(ocr_service,'resolve_dms_endpoint',return_value={'name':'fixture'}),patch.object(ocr_service,'ensure_image_bytes',side_effect=AssertionError('OCR path reached')) as image_step:
        try:ocr_service.recognize_id_card('preview@example.test',b'local fixture','fixture.png')
        except ocr_service.DmsOcrError as exc:assert exc.http_status==402 and exc.code=='insufficient_balance'
        else:raise AssertionError('Zero balance reached OCR')
        image_step.assert_not_called()
    print('PASS product HTTP billing transport and zero-balance OCR rejection before image/model processing')
finally:frappe.db.rollback();frappe.destroy()
