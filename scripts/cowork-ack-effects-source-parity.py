"""Offline fixed-source count rule comparison; no production imports or IO.

Extract only the source helper's AST, with its explicit common constant supplied.
The native helper is also extracted as pure code. Persistence is tested in Site.
"""
import ast
from copy import deepcopy
import hashlib
from pathlib import Path
from types import SimpleNamespace
import sys

root = Path(sys.argv[1])
source = root / 'services/erp/express_push/agent_store.py'
expected = '03d86de7de82761f33f386913d3d32a93874953a237a8151e02c4e5e3000c9bb'
assert hashlib.sha256(source.read_bytes()).hexdigest() == expected, 'Fixed source hash mismatch'
old = next(n for n in ast.parse(source.read_text()).body if isinstance(n, ast.FunctionDef) and n.name == '_bump_created_stock_masters')
old.body = [n for n in old.body if not isinstance(n, ast.ImportFrom)]
old.returns = None
for arg in old.args.args: arg.annotation = None
seen=[]
ns={'C': SimpleNamespace(ITEM_MODE_STOCK='stock_item'), 'bump_stock_master_count': lambda endpoint,n: seen.append(n)}
exec(compile(ast.fix_missing_locations(ast.Module(body=[old],type_ignores=[])),str(source),'exec'),ns)
p=Path(__file__).resolve().parents[1]/'apps/pearnly_cowork/pearnly_cowork/push/native/completion.py'
node=next(n for n in ast.parse(p.read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='created_stock_count')
new={'common':SimpleNamespace(ITEM_MODE_STOCK='stock_item')}
exec(compile(ast.Module(body=[node],type_ignores=[]),str(p),'exec'),new)
cases=[None,[],[{}],[{'mode':'stock_item','created':True}],
 [{'mode':'stock_item','created':False},{'mode':'direct_account','created':True}],
 [{'mode':'stock_item','created':1},{'mode':'stock_item','created':'yes'}],
 [{'mode':'stock','created':True}],[{'mode':'stock_item','created':True,'stkcod':'same'}]*2]
for modes in cases:
 seen.clear(); ns[old.name]('synthetic',deepcopy(modes))
 assert new[node.name]('legacy',deepcopy(modes)) == sum(seen)
 assert new[node.name]('managed',deepcopy(modes)) == 0
print(f'PASS {len(cases)} fixed-source legacy count cases; managed has no stock delta')
