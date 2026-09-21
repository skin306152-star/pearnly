"""Eight same-input Express comparisons using native collected facts.

Fixed-source texts arrive through stdin, hash-checked by the existing extractor.
Only dedicated local Site synthetic rows; source app/DB are never imported.
"""
from copy import deepcopy
from dataclasses import replace
import json
import importlib
from pathlib import Path
import runpy
import sys
from unittest.mock import patch
from uuid import uuid4
import frappe

parity = vars(importlib.import_module('cowork-express-source-parity'))
old = parity['SourceRules'](source_texts=json.load(sys.stdin))
fixture = runpy.run_path(str(Path(__file__).with_name('cowork-preparation-confirm-lab-verify.py')))
fixture['connect']()
cls = fixture['PreparationConfirmation']; cls.setUpClass()
from pearnly_cowork.push.application import service
from pearnly_cowork.push.express.prepare import prepare_express
from pearnly_cowork.push.express.preflight import preflight_express
cases = 0
try:
    for direction in ('purchase', 'sales'):
        for facts in ('empty', 'mapping', 'profile', 'bank'):
            test = cls('test_prepare_confirm_lease_exact_payload_and_idempotency'); test.setUp()
            try:
                frappe.set_user(test.manager)
                if facts == 'mapping': test.inputs.save_mapping('account', 'accounts_payable', 'R')
                if facts == 'profile': test.inputs.save_profile(test.workspace, '0107561000013', default_payment='cash')
                if facts == 'bank': test.bank.record(uuid4().hex, '2026-09-21', 'OUT' if direction=='purchase' else 'IN', '107')
                frappe.set_user(test.actor)
                pages = deepcopy(test.history['pages']); pages[0]['fields']['direction'] = direction
                test.history = test.storage.update_pages(test.history['name'], pages, test.history['record_version'])
                with patch.object(service, 'prepare_express', wraps=prepare_express) as invoke:
                    test.prepare()
                ep, h = deepcopy(invoke.call_args.args)
                ctx = invoke.call_args.kwargs['context']
                # Site/DocType IDs replace legacy integers. Normalize the company
                # identifier on both sides; compare all business fields unchanged.
                h['workspace_client_id'] = 7
                ep['user_id'] = 'synthetic-source-owner'
                ctx = replace(ctx, today=parity['TODAY'])
                previous = old.run(ep, h, ctx, None)
                current = preflight_express(ep, h, context=ctx)
                assert parity['plain'](previous) == parity['plain'](current), (direction,facts)
                assert current.ready, (direction,facts,current.reason)
                cases += 1
            finally:
                test.tearDown()
    print(json.dumps({'status':'PASS','cases':cases,'source_sha':'ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188','source_files':len(parity['SOURCE_HASHES'])}))
finally:
    frappe.db.rollback(); frappe.destroy()
