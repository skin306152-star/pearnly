"""Actual local session CAS against delayed OCR/dedup; all external IO is replaced."""
import json
import os
from pathlib import Path
from unittest.mock import patch

import frappe

os.chdir('/home/frappe/frappe-bench/sites')
SITE = 'dms-line-lab-b.localhost'
frappe.init(site=SITE, sites_path='.')
frappe.connect()
frappe.set_user('Administrator')

from pearnly_core import line_session as store
from pearnly_dms.intake import flow
from pearnly_dms.line import text_router

uid = 'Ulab-stale-work-fixture'
binding = {'tenant_id': SITE, 'user_id': 'local-fixture'}
checks = []
def check(label, value):
    assert value, label
    checks.append(label)
    print('PASS', label, flush=True)

try:
    assert store.get_session(uid) is None, 'Do not overwrite another draft'
    with patch.object(flow, 'current_binding', return_value=True), \
         patch.object(flow.out, 'start_loading'), patch.object(flow, '_enqueue') as enqueue, \
         patch.object(flow.out, 'start_loading_background', return_value=lambda: True), \
         patch.object(flow.line, 'download_message_content', return_value=b'local-image'), \
         patch.object(flow._id_ocr, 'editable_id_card', return_value={'name': 'local'}), \
         patch.object(flow, '_push') as push, patch.object(flow.line, 'reply'):
        flow.handle_image(binding, uid, 'old-image')
        flow.handle_image(binding, uid, 'new-image')
        with patch.object(flow._id_ocr, 'recognize_id_card') as ocr:
            flow.process_image(binding, uid, 'old-image')
            check('superseded queued image performs no OCR charge or request', not ocr.called)

        def cancelled(*a, **kw):
            store.clear_session(uid)
            return {'id': 'EP-local'}, {'id_card': {}}, None
        with patch.object(flow._id_ocr, 'recognize_id_card', side_effect=cancelled):
            flow.process_image(binding, uid, 'new-image')
        check('reset during OCR does not resurrect the old draft', store.get_session(uid) is None and not push.called)

        flow.handle_image(binding, uid, 'image-with-phone')
        text_router.capture_phone(binding, uid, 'local-token', store.get_session(uid), '0990000123')
        check('phone entry preserves pending image identity', store.get_session(uid)['payload']['pending_image'] == 'image-with-phone')
        with patch.object(flow._id_ocr, 'recognize_id_card', return_value=({'id': 'EP-local'}, {'id_card': {}}, None)), \
             patch.object(flow, 'run_dedup') as dedup:
            flow.process_image(binding, uid, 'image-with-phone')
        check('completed image uses the concurrently entered phone', dedup.call_args.args[3] == '0990000123')
        check('OCR image identity is retained for attachments', store.get_session(uid)['payload']['id_card_mid'] == 'image-with-phone')

        with patch.object(flow._id_ocr, 'recent_dms_customer_ids_by_tail', return_value=[]), \
             patch.object(flow.dms_intake, 'recognize_lookup') as lookup, patch.object(flow, '_send') as send:
            def replaced(*a, **kw):
                store.set_session(uid, 'menu', {'mode': 'booking'})
                return {'ok': True, 'scenario': 'none'}
            lookup.side_effect = replaced
            flow.run_dedup(binding, uid, {'name': 'local'}, '0990000123', 'EP-local')
            check('slow lookup cannot overwrite a newer menu choice', store.get_session(uid)['state'] == 'menu' and not send.called)
            lookup.reset_mock()
            flow.run_dedup(binding, uid, {'name': 'local'}, '0990000123', 'EP-local')
            check('stale queued lookup makes no external call', not lookup.called)

        expected = store.get_session(uid)
        store.set_session(uid, 'collecting', {'phone': 'new-phone'})
        check('snapshot compare and swap rejects a changed draft', not store.replace_session_snapshot(uid, expected, 'reviewing', {'nonce': 'old'}))
        check('rejected lookup leaves the new value intact', store.get_session(uid)['payload']['phone'] == 'new-phone')
    store.clear_session(uid)
    path = Path(SITE + '/private/line-lab-stale-work.json')
    path.write_text(json.dumps({'passed': len(checks), 'checks': checks, 'external_calls': 0, 'tested_at': frappe.utils.now()}, indent=2))
    path.chmod(0o600)
finally:
    frappe.destroy()
