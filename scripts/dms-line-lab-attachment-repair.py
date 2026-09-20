"""Append two synthetic test attachments to owned BK20; no order/payment creation.

Each upload simulates a transport timeout AFTER the real remote response, then
reads back instead of replaying. A second call must issue zero upload POSTs.
"""
import argparse
import json
import os
from pathlib import Path

import frappe
import requests

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--execute-lab-writes', action='store_true', required=True)
parser.parse_args()
site = 'dms-line-lab.localhost'
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site=site, sites_path='.')
frappe.connect()
frappe.set_user('Administrator')
from pearnly_dms.intake.service import run_logged_in
from pearnly_dms.ocr.ocr_service import resolve_dms_endpoint
from pearnly_dms.line import out
from pearnly_dms.booking.booking_readback import docno_equal

try:
    private = Path(site) / 'private'
    qa = json.loads((private / 'line-lab-fields-expected.json').read_text())
    content = out.download_content(qa['files']['slip_mid'])
    assert content and content[:4] == b'\x89PNG', 'Only the owned NO PAYMENT MADE fixture is allowed'
    user = frappe.db.get_value('DMS Operator', {'display_name': 'E2E DMS operator'}, 'user')
    log = frappe.get_doc('Pearnly Push Log', {'business_key': 'BK000002609000020', 'actor': user, 'status': 'success'})
    booking_id = str(json.loads(log.response_body)['booking_id'])
    filename = private / 'line-lab-attachment-repair.json'

    def trial(client, adapter):
        def read():
            return client._parse_form_defaults(client._post_text('drfcbc/form.php', {'status': 'e', 'id': booking_id}))
        before = read()
        assert docno_equal('BK000002609000020', before) and before['cusval'] == '127'
        assert before['txtregisname'].startswith('E2E')
        labels = ['E2E260920 recovery A NO PAYMENT', 'E2E260920 recovery B NO PAYMENT']
        assert not any(value in labels for key, value in before.items() if key.startswith('fulcurrname')), 'Already run; inspect evidence instead of replaying'
        filename.write_text(json.dumps({'stage': 'before_upload', 'booking_no': before['txtdocno']})); filename.chmod(0o600)
        files = [{'display_name': label, 'filename': 'e2e-no-payment.png', 'content_type': 'image/png', 'content': content} for label in labels]
        original = client.transport.post
        writes = []
        def timeout_after_commit(url, **kwargs):
            response = original(url, **kwargs)
            if url.endswith('/drfcbc/edit.php'):
                writes.append(kwargs['data']['fulnewname[]'])
                raise requests.Timeout('synthetic lost response AFTER actual upload')
            return response
        client.transport.post = timeout_after_commit
        try:
            first = client.attach_booking_files(booking_id=booking_id, files=files)
            first_writes = len(writes)
            second = client.attach_booking_files(booking_id=booking_id, files=files)
            after = read()
        finally:
            client.transport.post = original
        stable = {key: value for key, value in before.items() if not key.startswith('ful')}
        changed = [key for key, value in stable.items() if after.get(key) != value]
        checks = {'two_files_verified_despite_lost_response': first['ok'] and first['attached'] == 2,
                  'two_initial_uploads': first_writes == 2,
                  'retry_is_read_only': second['ok'] and len(writes) == first_writes,
                  'both_new_files_exist': all(label in after.values() for label in labels),
                  'previous_attachments_preserved': all(value in after.values() for key, value in before.items() if key.startswith('fulcurrname')),
                  'all_original_business_fields_preserved': not changed}
        return {'ok': all(checks.values()), 'checks': checks, 'business_fields_compared': len(stable),
                'changed_field_names': changed, 'upload_posts': len(writes), 'order_creates': 0}
    result = run_logged_in(resolve_dms_endpoint(user, None), trial)
    filename.write_text(json.dumps(result, ensure_ascii=False, indent=2)); filename.chmod(0o600)
    print(json.dumps(result), flush=True)
    assert result.get('ok')
finally:
    frappe.destroy()
