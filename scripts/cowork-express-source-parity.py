#!/usr/bin/env python3
"""Hash-bound, offline differential execution of deployed Express pure rules.

AST extraction keeps source function bodies intact. Only imports at IO seams are
bound to explicit in-memory evidence; no source application/framework is imported.
No credentials, network, DB, environment flags, or Companion are used.
"""
from __future__ import annotations
import argparse
import ast
from copy import deepcopy
from dataclasses import asdict, is_dataclass
from datetime import date, timedelta
from decimal import Decimal
import hashlib
import json
import logging
from pathlib import Path
import sys
from types import SimpleNamespace

from pearnly_cowork.push.express import common, mapper, sales_mapper, preflight, prepare
from pearnly_cowork.push.express import field_corrections, field_clean, catalog_resolver
from pearnly_cowork.push.express import posting_profile, stock_acc_group, account_rules
from pearnly_cowork.push.express.preflight import ExpressContext

SOURCE_HASHES = {'services/erp/erp_payload.py': 'fba845fae9caafb2b517f75725d0d3a92079bcb1a5e26eff2c6b266a19eaa34d',
 'services/erp/express_account_identity.py': '6271c1277c12bffbad09a02514ca29255bdb68f776106d5232f4f2241b8eaa4b',
 'services/erp/express_push/__init__.py': '127d05cb51b7c6218abe5befde8f6500ec4f88ea6a2aedb014b6f066d1c1e1d3',
 'services/erp/express_push/bank_evidence.py': '487b0c356d294c0758ccb519aac5499635203cdf85d5780995a57d9cb99a4634',
 'services/erp/express_push/catalog_resolver.py': 'ded39f9078cdbbeadc00ca71d92e9cffa7f7d5f602172500368e444fa18ddb60',
 'services/erp/express_push/common.py': 'e00c6410c711208cf2bcbf20579d9d67e365b2957aa9d90a3cd9f68e4fe09f58',
 'services/erp/express_push/direction.py': '808be2cb59be1f5270ae3e0d5b8aa16915b7603c2c8448a7df339dd80630ec8a',
 'services/erp/express_push/doc_sanity.py': '2c82e4b459ca6fd5b5f4db783fbf04db87e24c97c16fa748b4ff7dc10fd8d39c',
 'services/erp/express_push/enqueue.py': '234aaf0675ccd9e05ff697230b2f404574a088775129d5cb7a3a220a745d637a',
 'services/erp/express_push/mapper.py': '034cfa552af372f4ff7251ddd5795105a33f2cb74c7b021acc28b6df155d1c8d',
 'services/erp/express_push/payment.py': '35a8f862f4377bb03139847e4820b5ec2ef2145042c0d223958a8d2eb315e2c5',
 'services/erp/express_push/posting_kind.py': '4742aec35acfdcc04741300d57eb1fce0c7e9184f09d81767bc3df21e8cc0efa',
 'services/erp/express_push/posting_profile.py': 'bb03677be056466b1381779776dfa6d48c02d3cd541710242370420440f4f233',
 'services/erp/express_push/preflight.py': '5b5857f297b59c35227d35a40091af0969d870bad71aad735a12ed2f1019f407',
 'services/erp/express_push/prior_doc.py': '3a308c5092f0e5286d99758300be469c3d88d58ef0cda9dfc15cf0608596db79',
 'services/erp/express_push/sales_mapper.py': '207e9bf78c2d240b722978ea91204908b969597dd8d1d7092704332ffb359d1e',
 'services/erp/express_push/stock_acc_group.py': 'f037c43c15e57fc339e9a5fa54420fd5bf0b49e161340ebba8e1493a1c662a85',
 'services/erp/external_ref.py': '456836bd76a07773e58c64f35721d3be6923d3df89524e9c8b5ab664281ed1cc',
 'services/erp/internal_push_guard.py': 'b64ba33ae6a1dd8fda9319aaf485b0280125c6aff2b17b42c058f963a356f14c',
 'services/expense/confidence.py': '2687086ee6eef2901f5c7a00441bc273665ea67c6a227532af290b7ba12bc426',
 'services/expense/merchant.py': '53a2919307e2d16eabeeb082c2ec2f54c5fb20e8d184f2a52c32fe3a5a7022d7',
 'services/purchase/field_clean.py': '43912e65fcce3afa53fa4f7cdfd2d57afc02ff5556c5e17a6d76c2d030bdbed8',
 'services/purchase/intake.py': '2503692a09649cb12050ed3506bafa577e1303498e377791c5aff2d88a8b3a56',
 'services/purchase/item_verdict.py': '595e9491d372112fe6e140691b8b661f16f5c286ea9476695239537ab921eeb2',
 'services/purchase/ocr_corrections.py': '6f537f763d8aee5fb2313040b3add0768a0819bc213bfc1aadeab639608751cd',
 'services/purchase/totals.py': 'c530a8b2b89f03713d102d89dbacc27d826634621c551feacaf8dd3e382bd6ee',
 'services/sales_agg/vat.py': '88fab019e7e5ab03b689e9e1a83494bf96b4fb423d01067ed96371ea2d878167',
 'services/summary_import/columns.py': '085e94d6684c74044d1920fc8d678a96d2d2a76b9b9b3ba20f658f29a7492f18'}
TODAY = date(2026, 9, 21)

class FrozenDate(date):
    @classmethod
    def today(cls):
        return cls(2026, 9, 21)

class SourceRules:
    def __init__(self, root=None, source_texts=None):
        self.root = Path(root) if root else None
        self.source_texts = source_texts
        for path, expected in SOURCE_HASHES.items():
            actual = hashlib.sha256(self.read(path).encode("utf-8")).hexdigest()
            if actual != expected:
                raise ValueError(f'source hash mismatch: {path}')
        self.modules = {}
        self.context = None
        self._build()

    def read(self, path):
        if self.source_texts is not None:
            return self.source_texts[path]
        return (self.root / path).read_text()

    def lookup(self, module, name):
        return getattr(self.modules[module], name)

    def load(self, module, path, *, only=None, omit=(), bindings=None):
        tree = ast.parse(self.read(path))
        if only is not None:
            def selected(node):
                if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                    return node.name in only
                if isinstance(node, (ast.Assign, ast.AnnAssign)):
                    return any(isinstance(n, ast.Name) and n.id in only for n in ast.walk(node))
                return False
            tree.body = [n for n in tree.body if selected(n)]
        tree.body = [n for n in tree.body if getattr(n, 'name', None) not in omit]
        class Imports(ast.NodeTransformer):
            def visit_ImportFrom(self, node):
                if node.module and (node.module.startswith('services') or node.module == 'core'):
                    return [ast.Assign(targets=[ast.Name(id=a.asname or a.name, ctx=ast.Store())],
                           value=ast.Call(func=ast.Name(id='_lookup', ctx=ast.Load()),
                           args=[ast.Constant(node.module), ast.Constant(a.name)], keywords=[])) for a in node.names]
                return node
        tree = Imports().visit(tree)
        ns = dict(__name__=__name__, _lookup=self.lookup)
        exec('from typing import *\nfrom decimal import *\nfrom dataclasses import dataclass, field, asdict\nimport re, ntpath, json, logging\nfrom datetime import date, timedelta', ns)
        ns.update(bindings or {})
        exec(compile(ast.fix_missing_locations(tree), path, 'exec'), ns)
        ns['date'] = FrozenDate
        # Preserve globals dictionary used by compiled functions and expose the same values.
        target = self.modules.setdefault(module, SimpleNamespace())
        target.__dict__.update(ns)
        return target

    def _build(self):
        p='services.erp.express_push'
        self.load('services.expense.merchant','services/expense/merchant.py',only={'_BRAND_ALIASES','is_known_brand'})
        self.modules['services.expense']=SimpleNamespace(merchant=self.modules['services.expense.merchant'])
        fc=self.load('services.purchase.field_clean','services/purchase/field_clean.py',omit={'serialize_supplier'})
        self.modules['services.purchase']=SimpleNamespace(field_clean=fc)
        corr=self.load('services.purchase.ocr_corrections','services/purchase/ocr_corrections.py')
        self.load('services.purchase.intake','services/purchase/intake.py',only={'_GENERIC_BUYER_RE','judge_direction','doc_type_payment_hint','_has_buyer_identity'},bindings={'normalize_ocr_fields':corr.normalize_fields,'_to_decimal':fc.to_decimal})
        self.load('services.purchase.item_verdict','services/purchase/item_verdict.py')
        vat = self.load('services.sales_agg.vat','services/sales_agg/vat.py')
        self.load('services.purchase.totals','services/purchase/totals.py',only={'_VAT_INCL_NUM','vat_from_inclusive'},bindings={'VAT_PART':vat.VAT_PART,'GROSS_PART':vat.GROSS_PART})
        primitive=self.load(p+'.common','services/erp/express_push/common.py',only={'_d','parse_invoice_date'})
        self.load(p+'.bank_evidence','services/erp/express_push/bank_evidence.py',only={'_CENT','_WINDOW_DAYS','_DIR_FOR_DIRECTION','bank_paid_match'},bindings={'_d':primitive._d,'parse_invoice_date':primitive.parse_invoice_date})
        self.load(p+'.payment','services/erp/express_push/payment.py')
        self.load(p+'.common','services/erp/express_push/common.py')
        identity=self.load('services.erp.express_account_identity','services/erp/express_account_identity.py')
        account=self.load(p,'services/erp/express_push/__init__.py',only={'_path','authorized_account_sets','account_set_allowed','stock_lane_enabled','chart_codes'},bindings={'reported_account_set_roots':identity.reported_account_set_roots})
        account.express_push_enabled=lambda:self.context.enabled
        self.load('services.erp.external_ref','services/erp/external_ref.py')
        self.load(p+'.posting_kind','services/erp/express_push/posting_kind.py')
        self.load(p+'.posting_profile','services/erp/express_push/posting_profile.py')
        self.load(p+'.stock_acc_group','services/erp/express_push/stock_acc_group.py')
        self.load('services.summary_import.columns','services/summary_import/columns.py',only={'_THAI_MARKS','skeleton'})
        self.load(p+'.catalog_resolver','services/erp/express_push/catalog_resolver.py')
        self.load(p+'.prior_doc','services/erp/express_push/prior_doc.py',only={'attach_prior_docnum'},bindings={'prior_docnum':lambda *_:self.context.prior_docnum})
        self.purchase=self.load(p+'.mapper','services/erp/express_push/mapper.py')
        self.sales=self.load(p+'.sales_mapper','services/erp/express_push/sales_mapper.py')
        self.load('services.erp.erp_payload','services/erp/erp_payload.py',only={'flatten_history_for_mrerp'})
        self.load(p+'.doc_sanity','services/erp/express_push/doc_sanity.py')
        self.modules['services.expense'].confidence=self.load('services.expense.confidence','services/expense/confidence.py')
        account.direction=self.load(p+'.direction','services/erp/express_push/direction.py')
        self.modules['core']=SimpleNamespace(db=SimpleNamespace(
            get_user_tenant_id=lambda _:self.context.tenant_id,
            get_workspace_client=lambda *a,**k:{'tax_id':self.context.own_tax_id},
            get_mrerp_mappings_bundle=lambda _:deepcopy(self.context.mappings)))
        self.pf=self.load(p+'.preflight','services/erp/express_push/preflight.py')
        # The original prefetch interface bypasses all source bank/profile DB branches.
        self.load('services.erp.internal_push_guard','services/erp/internal_push_guard.py',only={'INTERNAL_SOURCES','is_internal_history','require_external_history'})
        self.enqueue=self.load(p+'.enqueue','services/erp/express_push/enqueue.py')

    def run(self, endpoint, history, context, kind):
        self.context=context
        fields=(history.get('pages') or [{}])[0].get('fields') or {}
        wcid=history.get('workspace_client_id')
        seller=fields.get('seller_tax') or fields.get('seller_tax_id')
        prefetch={'profiles':{wcid:{seller:deepcopy(context.supplier_profile)}},'bank_index':deepcopy(context.bank_index)}
        return self.pf.preflight_express(deepcopy(endpoint),deepcopy(history),prefetch=prefetch,posting_kind=kind)


def plain(value):
    if is_dataclass(value):return plain(asdict(value))
    if isinstance(value,dict):return {k:plain(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):return [plain(v) for v in value]
    if isinstance(value,date):return value.isoformat()
    return value

def capture(fn):
    try:return ('return',plain(fn()))
    except Exception as e:return ('raise',type(e).__name__,str(e))


def fixtures():
    cfg={'account_set':'DATAT','fallback_acc':'A','vat_input_acc':'V','ap_acc':'P',
         'revenue_acc':'S','vat_output_acc':'O','ar_acc':'R','cash_acc':'C'}
    fields={'seller_name':'บริษัท ทดสอบ จำกัด','seller_tax':'0107561000013','buyer_name':'Buyer Company',
            'buyer_tax':'0994000333444','subtotal':'100.00','vat':'7.00','total':'107.00',
            'invoice_number':'INV-001','document_type':'tax_invoice','posting_item_type_manual':'goods'}
    history={'id':'history-1','tenant_id':'tenant-1','workspace_client_id':7,'invoice_date':'2026-09-01',
             'invoice_no':'INV-001','total_amount':'107.00','confidence':'high','pages':[{'fields':fields}]}
    endpoint={'id':'ep1','user_id':'user1','adapter':'express','config':cfg}
    context=ExpressContext(True,TODAY,'0994000333444','tenant-1',{})
    return endpoint,history,context


def run(source=None, source_texts=None):
    old=SourceRules(source, source_texts)
    counter={}
    def check(group,label,a,b):
        actual,expected=capture(a),capture(b)
        if actual[0] == 'raise' and group != 'money':
            raise AssertionError(f'unexpected exception in {group}/{label}: {actual!r}')
        if actual!=expected:
            raise AssertionError(f'{group}/{label}\nNEW {actual!r}\nOLD {expected!r}')
        counter[group]=counter.get(group,0)+1
    from dataclasses import replace
    endpoint,history,ctx=fixtures()
    changes=[({}, {}, {}, {})]
    # Full ordered gate results, successful wire bodies and explicit negative outcomes.
    for key,values in {
        'document_type':['receipt','simplified_tax_invoice','payment_evidence','credit_note','tax_invoice','unknown'],
        'currency':['THB','USD','บาท',''], 'notes':['deposit','reissue',''],
        'buyer_tax':['','0107561000013','123'], 'seller_tax':['','123','0994000333444'],
        'seller_name':['','123','Vendor😀公司','711'],
        'payment_status':['paid','unpaid','cash','credit',None],
        'posting_payment_manual':['cash','credit','bad'],
        'posting_item_type_manual':['expense','goods',''],
        # 旧线上汇总表批量建单的合成票种标记（vat_checkbox）随该功能删除，不再对照（有意变化，2026-09-28）。
        'document_type_source':['document'],
        'items':[[],[{'name':'A','amount':'100'}],[{'name':'A','amount':'101.01'}],
                 [{'name':'A','amount':'99'}],[{'amount':'100'}],
                 [{'name':'A','amount':'107','quantity':'0'}],
                 [{'name':'A','amount':'33.33'},{'name':'B','amount':'66.67'}]],
    }.items():
        changes += [({key:v},{},{},{}) for v in values]
    for key,values in {'confidence':['low','medium','needs_review','',None],
                       'direction':['sales','purchase','bogus','income','expense'],
                       'invoice_date':['1999-01-01','2027-01-01','2026-09-21','bad'],
                       'invoice_no':['','bad😀','2026-09-01'],
                       'merged_fields':[{'opening_stock':[{'name':'A','qty':'1','unit_cost':'2'}]}]}.items():
        changes += [({}, {key:v}, {}, {}) for v in values]
    for key,values in {'account_set':['','OTHER'],'max_payload_version':[0,1,'dirty'],
                       'directions':['sales','purchase',['sales'],[]],
                       'fallback_acc':['','X'],'revenue_acc':['','Y'],'ap_acc':[''],
                       'reported_accounts':[[{'code':'A'}],[],[{'code':x} for x in 'AVPSOCR']],
                       'account_set_row':['bad','2'],'posting_profile':[{'inventory_method':'perpetual'}]}.items():
        changes += [({}, {}, {key:v}, {}) for v in values]
    changes += [({}, {}, {},x) for x in ({'enabled':False},{'own_tax_id':''},{'prior_docnum':'RR123'},
                    {'supplier_profile':{'posting_payment':'cash'}},
                    {'bank_index':[{'direction':'OUT','amount':'107','tx_date':'2026-09-01'}]})]
    for i,(fc,hc,cc,xc) in enumerate(changes):
        # 有意的变化（用户 2026-09-27）：识别置信度不再拦推送，旧版低置信度一律 low_confidence。
        if hc.get('confidence', 'high') not in ('high', 'auto'):
            continue
        ep,h=deepcopy(endpoint),deepcopy(history);ep['config'].update(cc);h.update(hc);h['pages'][0]['fields'].update(fc)
        context=replace(ctx,**xc)
        for kind in (None,'service','stock'):
            old.context=context
            check('preflight',f'{i}:{kind}',lambda:preflight.preflight_express(ep,h,context=context,posting_kind=kind),lambda:old.run(ep,h,context,kind))
            flat=deepcopy(h);flat['fields']=h['pages'][0]['fields'];flat.pop('pages',None)
            for newfun,oldfun,name in [(mapper.build_express_payload,old.purchase.build_express_payload,'purchase'),(sales_mapper.build_express_sales_payload,old.sales.build_express_sales_payload,'sales')]:
                check(name,f'{i}:{kind}',lambda:newfun(deepcopy(flat),config=deepcopy(ep['config']),today=TODAY,prior_docnum=context.prior_docnum,posting_kind=kind,mappings=deepcopy(context.mappings)),lambda:oldfun(deepcopy(flat),config=deepcopy(ep['config']),posting_kind=kind,mappings=deepcopy(context.mappings)))
    # Final enqueue decision, including mismatch/incomplete cases outside pf.ready.
    for i,(fc,hc,cc,xc) in enumerate(changes):
        # 有意的变化（用户 2026-09-27）：识别置信度不再拦推送，旧版低置信度一律 low_confidence。
        if hc.get('confidence', 'high') not in ('high', 'auto'):
            continue
        ep,h=deepcopy(endpoint),deepcopy(history);ep['config'].update(cc);h.update(hc);h['pages'][0]['fields'].update(fc)
        context=replace(ctx,**xc);old.context=context
        def old_decision():
            result=old.enqueue.enqueue_express(ep,deepcopy(h),prefetch={'bank_index':context.bank_index,'profiles':{7:{'0107561000013':context.supplier_profile}}})
            msg=result.get('error_msg') or ''
            if msg=='ERR_EXPRESS_DISABLED':return ('disabled',msg,result['request_body'])
            if msg.startswith('EXPRESS_MANUAL: '):return ('manual',msg.split(': ',1)[1],result['request_body'])
            assert msg=='EXPRESS_QUEUED'
            return ('ready',None,result['request_body'])
        def new_decision():
            result=prepare.prepare_express(ep,h,context=context)
            return (result.disposition,result.reason,result.payload)
        check('prepare',str(i),new_decision,old_decision)
    oldcommon=old.modules['services.erp.express_push.common']
    for n in ['2.675','-2.675','0.01','0','-0.01','100.005','99999999999999999.995','NaN','Infinity','bad',None]:
        for vat in ['0','0.01','7',None]:
            fields={'total':n,'vat':vat};h={'total_amount':n}
            check('money',f'{n}:{vat}',lambda:common.amounts(fields,h),lambda:oldcommon.amounts(fields,h))
    oc=old.modules['services.purchase.ocr_corrections']
    for v in ['1/2/69','21/9/26','1/2/40','2569-01-01','2027-01-01','2020-01-01','bad','']:
        data={'date':v,'seller_name':'711','subtotal':'2.675','vat':'0.01','total':'2.685'}
        check('field_corrections',v,lambda:field_corrections.normalize_fields(data,today=TODAY),lambda:oc.normalize_fields(data))
    for mode in ['non_stock','stock','manual_review','invalid']:
        for count in [0,1,20,100]:
            config={'posting_profile':{'posting_mode':mode},'catalog_fingerprint':{'stmas_stock':count,'stcrd_lines':100,'stcrd_lines_moving_stock':count}}
            check('profile',f'{mode}:{count}',lambda:posting_profile.profile_from_config(config),lambda:old.modules['services.erp.express_push.posting_profile'].profile_from_config(config))
    for groups in [None,[],[{'acccod':'A','fit':True}],[{'acccod':'A','fit':False}], [{'acccod':'A','fit':True},{'acccod':'B','fit':True}]]:
        for chosen in ['', 'A','C']:
            config={'reported_stock_acc_groups':groups,'stock_acccod':chosen}
            check('stock_group',repr(config),lambda:stock_acc_group.resolve_stock_acc_group(config),lambda:old.modules['services.erp.express_push.stock_acc_group'].resolve_stock_acc_group(config))
    for name in ['Milk','milk','น้ำแข็ง หลอด','นาแขงหลอด','missing','']:
        products=[{'name':'Milk','code':'M'},{'name':'น้ำแข็งหลอด','code':'T'}]
        check('catalog',name,lambda:catalog_resolver.resolve_product(name,products),lambda:old.modules['services.erp.express_push.catalog_resolver'].resolve_product(name,products))
    for fields in [{'payment_status':p,'document_type':d,'posting_payment_manual':m} for p in ['', 'paid','credit'] for d in ['', 'tax_invoice','receipt'] for m in ['', 'cash','credit']]:
        for direction in ['purchase','sales']:
            options={'direction':direction,'profile':{'default_payment':'cash'},'bank_index':[{'direction':'OUT','amount':'107','tx_date':'2026-09-01'}],'total':'107','invoice_date':'2026-09-01'}
            check('payment',repr((fields,direction)),lambda:common.payment_verdict(fields,**options),lambda:oldcommon.payment_verdict(fields,**options))
    print(json.dumps({'source':'ec2e5c957e6b','hashes':len(SOURCE_HASHES),'cases':counter,'total':sum(counter.values()),'status':'PASS'},ensure_ascii=False))

if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--source')
    group.add_argument('--source-stdin', action='store_true', help='JSON relativePath -> fixed source text')
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    run(args.source, json.load(sys.stdin) if args.source_stdin else None)
