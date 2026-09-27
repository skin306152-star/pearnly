"""Local stocktake acceptance: writes synthetic records only in the isolated lab.
No external calls. Fixtures remain in the task-owned volumes until teardown.
"""
import importlib.util
import json
from pathlib import Path
import sys
import uuid
from io import BytesIO
from unittest.mock import patch
from openpyxl import load_workbook
import frappe

spec = importlib.util.spec_from_file_location('fixtures', Path(__file__).with_name('erp-pos-lab-products-verify.py'))
fx = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fx)


def main():
    fx.connect()
    frappe.set_user('Administrator')
    sys.path.insert(0, fx.CONTROL_APP)
    from pearnly_control.features import codes_of, roles_payload
    from pearnly_core.provisioning import ensure_feature_roles
    from pearnly_erp_portal.products import service as products
    from pearnly_erp_portal.inventory import count, stock, warehouses, api
    fx.ensure_member(fx.OWNER, [])
    fx.ensure_member(fx.OUTSIDER, [])
    ensure_feature_roles(json.dumps(roles_payload('erp', codes_of('erp'))), admin_user=fx.OWNER)
    frappe.set_user(fx.OWNER)
    run='WB-'+uuid.uuid4().hex[:8]
    product=products.create_product(product_name=run, barcode='88'+uuid.uuid4().hex[:10],
        qr_payload=run+' QR text', unit='件', unit_price=10, default_cost=6)['name']
    wh=warehouses.resolve(run+' 仓库', create=True)
    for place,qty in [('A',10),('B',5),('D',2)]:
        stock.apply_movement(product=product,warehouse=wh,location=place,movement_type='stock_in',qty=qty,unit_cost=6)
    task=count.create_count(title=run,warehouse=wh)['name']
    rows={r['location']:r for r in count.list_items(count=task,limit=0)}
    lookup=count.lookup_count(count=task,product=product)
    a=count.add_entry(count=task,item=rows['A']['name'],qty=3,location='A',request_id=run+'-1',stock_token=lookup['stock_token'])
    b=count.add_entry(count=task,item=rows['A']['name'],qty=7,location='C',request_id=run+'-2')
    assert count.add_entry(count=task,item=rows['A']['name'],qty=7,location='C',request_id=run+'-2')['deduped']
    def denied(fn, cls=frappe.ValidationError):
        try: fn()
        except cls: return
        raise AssertionError('expected rejection')
    denied(lambda:count.add_entry(count=task,item=rows['A']['name'],qty=8,location='C',request_id=run+'-2'))
    denied(lambda:count.add_entry(count=task,item=rows['A']['name'],qty='NaN'))
    denied(lambda:count.edit_entry(entry=a['entry'],qty=2,version=9))
    count.edit_entry(entry=b['entry'],qty=8,location='E',version=1)
    count.edit_entry(entry=b['entry'],qty=7,location='C',version=2)
    count.add_entry(count=task,item=rows['B']['name'],qty=4,location='B')
    review=count.review_count(task)
    assert review['location_total']==1 and not review['conflicts'], review
    arow=next(r for r in review['items'] if r['name']==rows['A']['name'])
    assert arow['book_qty']==10 and arow['counted_qty']==10 and arow['difference']==0 and arow['location_difference']
    assert 'C' in arow['actual_positions']
    uncounted=next(r for r in review['items'] if r['location']=='D')
    assert uncounted['counted_qty'] is None and uncounted['difference'] is None
    assert uncounted['difference_value'] is None
    assert next(r for r in review['items'] if r['location']=='B')['difference_value']==-6
    report=api._count_workbook(review['task'],review['items'],review['entries'])
    assert report[:2]==b'PK'
    exported=load_workbook(BytesIO(report),read_only=True)
    detail=list(exported['Entries'].values)
    assert all(r[0] and r[1]==run and r[6]=='件' for r in detail[1:]), detail
    summary=list(exported['Summary'].values)
    assert next(r for r in summary[1:] if r[5]=='D')[8:11]==(None,None,None)
    exported.close()
    before=frappe.db.count('ERP Stock Movement')
    result=count.close_count(task,review_token=review['review_token'])
    assert result['status']=='Posted'
    assert {p:float(stock.current_balance(product,wh,p).qty) for p in ['A','B','C','D']}=={'A':3,'B':4,'C':7,'D':2}
    assert sum(stock.current_balance(product,wh,p).value for p in ['A','B','C','D'])==96
    movement_count=frappe.db.count('ERP Stock Movement')
    assert movement_count>before
    # 盘点差异走**自己的编号定义**（AD 段），不借入库 IR / 出库 IS：
    # 与 MR.ERP 的 stktranmod（调整是第三个模块、有自己的定义）同形。
    posted_entries=frappe.get_all('ERP Stock Entry',{'ref_type':'count','ref_id':task},pluck='doc_no')
    assert posted_entries and all(str(n or '').startswith('AD') for n in posted_entries),posted_entries
    assert count.close_count(task,review_token=review['review_token'])['deduped']
    assert frappe.db.count('ERP Stock Movement')==movement_count
    for call in [lambda:count.add_entry(count=task,item=rows['A']['name'],qty=1),
                 lambda:count.edit_entry(entry=a['entry'],qty=5),lambda:count.delete_count(task),
                 lambda:count.apply_import(count=task,rows=[]),lambda:count.recount_product(count=task,product=product)]: denied(call)
    for doctype,name in [('ERP Stock Count',task),('ERP Stock Count Item',rows['A']['name']),('ERP Stock Count Entry',a['entry'])]:
        def mutate():
            doc=frappe.get_doc(doctype,name);doc.set('note','tamper');doc.save(ignore_permissions=True)
        denied(mutate)
    assert not count.review_count(task)['conflicts']
    # Stock moves before save and after save are both detected, including net-zero changes.
    second=count.create_count(title=run+' concurrent',warehouse=wh)['name']
    row=next(r for r in count.list_items(count=second,limit=0) if r['location']=='A')
    token=count.lookup_count(count=second,product=product)['stock_token']
    stock.apply_movement(product=product,warehouse=wh,location='A',movement_type='stock_out',qty=1)
    denied(lambda:count.add_entry(count=second,item=row['name'],qty=2,stock_token=token))
    e=count.add_entry(count=second,item=row['name'],qty=2)
    assert count.list_items(count=second,limit=0)[0]['basis_set'] in (0,1)
    old_review=count.review_count(second)['review_token']
    count.edit_entry(entry=e['entry'],note='review changes',version=1)
    denied(lambda:count.close_count(second,review_token=old_review))
    stock.apply_movement(product=product,warehouse=wh,location='A',movement_type='stock_out',qty=1)
    stock.apply_movement(product=product,warehouse=wh,location='A',movement_type='stock_in',qty=1,unit_cost=6)
    denied(lambda:count.close_count(second))
    assert count.review_count(second)['conflicts']
    count.recount_product(count=second,product=product)
    count.add_entry(count=second,item=row['name'],qty=2)
    count.close_count(second,review_token=count.review_count(second)['review_token'])
    assert stock.current_balance(product,wh,'A').qty==2
    # Corrected actual warehouse, swapped positions and costs, and a real zero count.
    swap=count.create_count(title=run+' swap',warehouse=wh)['name']
    rs={r['location']:r for r in count.list_items(count=swap,limit=0)}
    count.add_entry(count=swap,item=rs['A']['name'],qty=2,location='B')
    count.add_entry(count=swap,item=rs['B']['name'],qty=4,location='A')
    count.add_entry(count=swap,item=rs['D']['name'],qty=0,location='D')
    count.close_count(swap)
    assert stock.current_balance(product,wh,'A').qty==4
    assert stock.current_balance(product,wh,'B').qty==2
    assert stock.current_balance(product,wh,'D').qty==0
    # Distinct source costs and a corrected warehouse must survive relocation.
    valued=products.create_product(product_name=run+' valued',unit='件')['name']
    other_wh=warehouses.resolve(run+' 实际仓库', create=True)
    for loc,qty,cost in [('A',2,3),('B',4,11)]:
        stock.apply_movement(product=valued,warehouse=wh,location=loc,
                             movement_type='stock_in',qty=qty,unit_cost=cost)
    valuation=count.create_count(title=run+' valuation',warehouse=wh)['name']
    vr={r['location']:r for r in count.list_items(count=valuation,limit=0) if r['product']==valued}
    va=count.add_entry(count=valuation,item=vr['A']['name'],qty=2,warehouse=other_wh,location='B')
    count.add_entry(count=valuation,item=vr['B']['name'],qty=4,location='A')
    # Serialized flags cannot enable raw document writes that bypass the service.
    raw=frappe.get_doc('ERP Stock Count Entry',va['entry'])
    raw.flags.stock_count_service=True
    raw.qty=999
    denied(lambda:raw.save(ignore_permissions=True))
    frappe.db.commit()
    original=stock.apply_movement
    calls=[]
    def fail_second(**args):
        calls.append(args)
        if len(calls)==2: raise RuntimeError('simulated posting failure')
        return original(**args)
    with patch.object(stock,'apply_movement',side_effect=fail_second):
        denied(lambda:count.close_count(valuation),RuntimeError)
    frappe.db.rollback()
    assert frappe.db.get_value('ERP Stock Count',valuation,'status')=='Active'
    # 盘点差异现在开**入库/出库单据**（带 IR/IS 号），所以"没留下东西"要按单据查：
    # 流水挂在单据下、单据指回这份盘点。失败整批回滚，两边都不该有残留。
    assert not frappe.db.count('ERP Stock Entry',{'ref_type':'count','ref_id':valuation})
    assert not frappe.db.count('ERP Stock Movement',{'reason':count.stock_documents.adjustment_note(frappe.db.get_value('ERP Stock Count',valuation,'title'))})
    assert stock.current_balance(valued,wh,'A').qty==2
    count.close_count(valuation)
    assert stock.current_balance(valued,wh,'A').value==44
    assert stock.current_balance(valued,wh,'B').qty==0
    assert stock.current_balance(valued,other_wh,'B').value==6
    stock.apply_movement(product=product,warehouse=wh,location='B',movement_type='stock_in',qty=1,unit_cost=99)
    assert next(r for r in count.review_count(task)['items'] if r['location']=='B')['difference_value']==-6
    # Unknown QR does not create rows; internal whitespace is significant.
    assert count.lookup_count(count=swap,code=run+' QR text')['product']['name']==product
    denied(lambda:count.lookup_count(count=swap,code=run+'QRtext'))
    frappe.db.commit()
    for user in [fx.OUTSIDER,'Guest']:
        frappe.set_user(user)
        denied(lambda:count.review_count(task),frappe.PermissionError if user!='Guest' else frappe.AuthenticationError)
    frappe.set_user(fx.OWNER)
    print(json.dumps({'ok':True,'run':run,'task':task,'product':product,'checks':[
        'split positions','quantity and location differences','idempotent save/finish','request conflict',
        'stale edit','non-finite rejection','export','untouched uncounted','closed native/API write guards',
        'movement before save','movement after count incl net zero','stale review','audited recount',
        'position swap','zero count','exact QR','outsider/guest','exported labels',
        'different source costs','actual warehouse','native flag spoof rejected','atomic posting rollback',
        'uncounted export stays blank','finished variance valuation is frozen']},ensure_ascii=False))
    frappe.destroy()

if __name__=='__main__': main()
