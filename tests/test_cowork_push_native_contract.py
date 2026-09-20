"""Pure native-ledger identities, frozen requests and declared service boundary."""
import ast
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import unittest

from pearnly_cowork.push.native.contracts import (
    PushLedgerError, ReservationContext, business_identity, document_identity,
    duplicate_reason, encode, frozen_values, request_identity, utc, writer_identities,
)


class NativePushContract(unittest.TestCase):
    def setUp(self):
        self.context = ReservationContext('actor', 'workspace', 'client', 'history', 1,
            'connection', 'DATAT', connection_revision=1)
        self.target = {'endpoint_id': 'connection', 'account_set': 'DATAT', 'root_key': r'D:\EXP',
            'binding_generation': 0, 'adapter': 'express', 'request_intent': {'posting_kind': 'service'}}

    def test_request_identity_is_actor_workspace_scoped(self):
        value = request_identity(self.context, 'request')
        self.assertEqual(value, request_identity(replace(self.context, history_version=2), 'request'))
        self.assertNotEqual(value, request_identity(replace(self.context, actor='other'), 'request'))
        self.assertNotEqual(value, request_identity(replace(self.context, workspace='other'), 'request'))

    def test_account_blank_is_lookup_only(self):
        context = replace(self.context, account_key='')
        self.assertTrue(request_identity(context, 'request'))
        for function, args in [(document_identity,()),(writer_identities,(self.target,)),
                (business_identity,('INV','Seller')),(frozen_values,(self.target,{},'prepared','',[]))]:
            with self.subTest(function=function.__name__), self.assertRaises(PushLedgerError):
                function(context,*args)

    def test_case_path_identity_is_not_unicode_casefold(self):
        self.assertEqual(document_identity(self.context), document_identity(replace(self.context, account_key='datat')))
        self.assertNotEqual(document_identity(replace(self.context, account_key='ß')),
                            document_identity(replace(self.context, account_key='SS')))

    def test_history_scope_remains_part_of_dedup_key(self):
        for changes in ({'client':'other'},{'workspace':'other'},{'history':'other'},{'connection':'other'}):
            self.assertNotEqual(document_identity(self.context), document_identity(replace(self.context,**changes)))

    def test_business_success_identity_is_exact_and_owner_scoped(self):
        key=business_identity(self.context,'INV','Seller')
        for context,inv,seller in [(replace(self.context,actor='other'),'INV','Seller'),
                (self.context,'inv','Seller'),(self.context,'INV','seller')]:
            self.assertNotEqual(key,business_identity(context,inv,seller))
        self.assertEqual(key,business_identity(replace(self.context,history='another'),'INV','Seller'))
        self.assertIsNone(business_identity(self.context,'','Seller'))

    def test_writer_serializes_endpoint_and_company_account_without_client_split(self):
        first=dict(writer_identities(self.context,self.target))
        other=dict(writer_identities(replace(self.context,client='other',connection='other'),self.target))
        self.assertEqual(len(set(first)&set(other)),1)
        self.assertEqual(first[next(iter(set(first)&set(other)))],'account')
        self.assertEqual(len(writer_identities(self.context,self.target)),2)

    def test_frozen_intent_and_payload_both_bound(self):
        first=frozen_values(self.context,self.target,{'amount':'2.675'},'prepared','',[])
        self.assertEqual(json.loads(first['target_json'])['request_intent'],self.target['request_intent'])
        other=frozen_values(self.context,self.target,{'amount':'2.68'},'prepared','',[])
        self.assertNotEqual(first['request_fingerprint'],other['request_fingerprint'])
        self.target['request_intent']['posting_kind']='stock'
        self.assertNotEqual(first['request_fingerprint'],frozen_values(self.context,self.target,{'amount':'2.675'},'prepared','',[])['request_fingerprint'])

    def test_target_binding_and_final_states_cannot_be_client_supplied(self):
        for change in ({'endpoint_id':'other'},{'account_set':'other'},{'binding_generation':1},{'adapter':'mrerp'}):
            with self.subTest(change=change),self.assertRaises(PushLedgerError):
                frozen_values(self.context,{**self.target,**change},{},'prepared','',[])
        for state in ('pending','leased','success','cancelled'):
            with self.assertRaises(PushLedgerError):frozen_values(self.context,self.target,{},state,'',[])

    def test_generation_context_and_json_nonfinite_are_rejected(self):
        for changes in ({'branch':'legacy','binding_generation':1},{'branch':'managed','binding_generation':0},
                {'history_version':True},{'history_version':0},{'connection_revision':-1}):
            with self.assertRaises(PushLedgerError):replace(self.context,**changes)
        with self.assertRaises(PushLedgerError):encode({'amount':float('nan')})

    def test_unknown_and_success_never_mean_retryable(self):
        expected={'prepared':'active','pending':'active','leased':'active','success':'already_success',
                  'manual':None,'cancelled':None}
        for state,reason in expected.items():self.assertEqual(duplicate_reason({'status':state}),reason)
        self.assertEqual(duplicate_reason({'status':'manual','result_unknown':1}),'unknown')

    def test_utc_requires_aware_input(self):
        with self.assertRaises(PushLedgerError):utc(datetime(2026,9,21))
        self.assertEqual(utc(datetime(2026,9,21,tzinfo=timezone.utc)),datetime(2026,9,21))

    def test_schema_has_durable_unique_guards_and_no_rpc_consumer(self):
        root=Path(__file__).resolve().parents[1]/'apps/pearnly_cowork/pearnly_cowork'
        record=json.loads((root/'pearnly_cowork/doctype/cowork_push_record/cowork_push_record.json').read_text())
        fields={f['fieldname']:f for f in record['fields']}
        self.assertTrue(fields['request_digest']['unique'])
        self.assertTrue(fields['active_key']['unique'])
        self.assertIn('cancelled',fields['status']['options'])
        for path in (root/'push/native').glob('*.py'):
            tree=ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)):
                    self.assertFalse(any('whitelist' in ast.unparse(d) for d in node.decorator_list),path)

if __name__=='__main__':unittest.main()
