"""Explicit Windows TEST-only integration using a fixed legacy source bundle.

Copy beside the hash-bound src/ and packaging/ trees. Default inspection and
report modes are read-only on ERP. Never load or save the active ProfileStore.
The write mode requires the original same-session single-instance guard and a
separately confirmed native task. It polls once and never retries an ERP write.
"""
import argparse
from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import sys
import subprocess
import time

TARGET = r'\\Accserver\d$\ACCOUNT\70EXP\TEST'
ROOT = r'\\Accserver\d$\ACCOUNT\70EXP'
EXPECTED_TAX = '0735527000289'
EXPECTED_RUNNER = 'b9358ef8785b02447e9f42254128afcc38b96e498e72df99831e24202f36509b'
BASE = Path(__file__).resolve().parent
ORIGINAL_ENV = os.environ.copy()
PROFILE = Path(r'C:\Users\skin3\AppData\Roaming\Pearnly\companion.json')
EXE = Path(r'C:\Program Files\Pearnly Companion\companion.exe')
EXPECTED_EXE = '0be7e908c3ce38d080f88b57f6eb8bd59f51d0315c3da5ac58dd775b75be448a'
WRITE_STATE = {'writer_invoked': False, 'erp_writes': 0}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def verify_bundle():
    require(sys.platform == 'win32' and os.environ.get('USERNAME', '').lower() == 'skin3',
            'Interactive skin3 Windows identity required')
    require(BASE.name == 'PearnlyCoworkLab-20260921-17', 'Explicit task-owned directory required')
    manifest = json.loads((BASE/'source-manifest.json').read_text())
    require(manifest['source_sha'] == '33af0fd5e381e87802c95655dc35e7709eb2a233', 'Wrong source version')
    for name, digest in manifest['files'].items():
        path = BASE/name
        require(path.is_file() and not path.is_symlink() and BASE in path.resolve().parents,
                'Unsafe bundle path')
        require(hashlib.sha256(path.read_bytes()).hexdigest() == digest, 'Source bundle changed')
    require(hashlib.sha256((BASE/'packaging/native/cdx_reindex_runner.exe').read_bytes()).hexdigest()
            == EXPECTED_RUNNER, 'Wrong native CDX runner')
    # Isolate logging/config directories. The named Windows singleton itself is
    # independent of APPDATA and retains the deployed name, in the same session.
    os.environ['APPDATA'] = str(BASE/'private-appdata')


def inventory():
    result = {'python': sys.version.split()[0], 'modules': {}}
    for module in ('dbf', 'httpx', 'pydantic', 'pywinauto', 'pywin32', 'PySide6'):
        try:
            result['modules'][module] = importlib.metadata.version(module)
        except importlib.metadata.PackageNotFoundError:
            result['modules'][module] = None
    return result


def collect():
    from src.companion.account_probe import (probe_account_sets, _read_isinfo,
        chart_accounts_payload, probe_default_accounts)
    from src.companion.catalog_probe import catalog_payload, _iter_records, _gv
    from src.companion.fiscal_period import load
    from src.companion import account_lock, single_instance
    sets = probe_account_sets(ROOT, include_details=False)
    selected = [item for item in sets if os.path.normcase(item.path) == os.path.normcase(TARGET)]
    require(len(selected) == 1, 'TEST must be an unambiguous registered account')
    company, tax = _read_isinfo(Path(TARGET))
    require(company and tax == EXPECTED_TAX, 'TEST company identity changed')
    mapping, accounts = probe_default_accounts(TARGET), chart_accounts_payload(TARGET)
    require(len(mapping) == 6 and accounts, 'Real TEST account mappings required')
    catalog = catalog_payload(TARGET)
    supplier = None
    for row in _iter_records(TARGET, 'APMAS'):
        name, tax = _gv(row, 'SUPNAM'), _gv(row, 'TAXID')
        if name and len(tax) == 13 and tax != EXPECTED_TAX:
            supplier = {'code': _gv(row, 'SUPCOD'), 'name': name, 'tax': tax,
                        'branch': _gv(row, 'ORGNUM') or '00000'}
            break
    require(supplier is not None, 'Existing TEST supplier required; do not invent a master')
    calendar = load(TARGET)
    require(calendar and calendar.locate(date(2026, 9, 21)), 'TEST fiscal period must cover test date')
    guard = single_instance.acquire()
    held = guard is None
    if guard is not None:
        require(guard._win_handle is not None, 'Singleton acquisition failed open')
        guard.release()
    facts = {'selected': {'account_set': TARGET, 'account_dir': TARGET, 'express_root': ROOT,
        'account_company': company, 'account_set_row': selected[0].row}, 'mapping': mapping,
        'accounts': accounts, 'catalog': catalog, 'supplier': supplier,
        'account_sets': [{'code': selected[0].code, 'name': selected[0].name, 'company': company,
            'tax_id': EXPECTED_TAX, 'path': TARGET, 'root': ROOT,
            'row': selected[0].row, 'writable': os.access(TARGET, os.W_OK), 'mapping': mapping}]}
    (BASE/'private-facts.json').write_text(json.dumps(facts, ensure_ascii=False), encoding='utf-8')
    return {'target': TARGET, 'company_present': True, 'tax_matches': True,
        'registered': True, 'chart_accounts': len(accounts), 'mapping_count': len(mapping),
        'products': len(catalog.get('products', [])), 'customers': len(catalog.get('customers', [])),
        'fiscal_date_covered': True, 'singleton_already_held': held,
        'account_busy': account_lock.is_account_busy(TARGET)}


def client():
    from src.companion.queue_client import QueueClient, QueueClientConfig
    config = json.loads((BASE/'private-connection.json').read_text(encoding='utf-8'))
    require(config['backend_url'] == 'https://192.168.0.93:43917', 'Only the explicit task TLS endpoint is allowed')
    require(config['target'] == TARGET and config['token'].startswith('exp_'), 'Wrong TEST connection')
    cert = BASE/'lab-server.pem'
    require(hashlib.sha256(cert.read_bytes()).hexdigest() == config['server_cert_sha256'], 'Wrong lab TLS certificate')
    os.environ['SSL_CERT_FILE'] = str(cert)
    os.environ['NO_PROXY'] = '192.168.0.93'
    return QueueClient(QueueClientConfig(config['backend_url'], config['token'],
                                       'cowork-windows-' + config['run_id'], timeout=60)), config


def report():
    from src.companion.payload_contract import SUPPORTED_PAYLOAD_VERSIONS
    facts = json.loads((BASE/'private-facts.json').read_text(encoding='utf-8'))
    qc, config = client()
    try:
        result = qc.health(account_sets=facts['account_sets'], accounts=facts['accounts'],
            selected=facts['selected'], mapping=facts['mapping'], catalog=facts['catalog'],
            method='dbf', companion_version='1.1.77', max_payload_version=max(SUPPORTED_PAYLOAD_VERSIONS))
        require(result.get('ok') is True and result.get('endpoint_id') == config['connection'],
                'Report must reach only the new native test connection')
        return {'ok': True, 'endpoint_matches': True, 'top_level_contract': 'message' not in result}
    finally:
        qc.close()


def poll_empty():
    qc, _ = client()
    try:
        require(qc.poll() is None, 'Unexpected task: do not execute or acknowledge it')
        return {'jobs': 0}
    finally:
        qc.close()


def write_once():
    from src.companion import single_instance, account_lock, cdx_reindex
    from src.companion.account_probe import _read_isinfo
    from src.companion.config import CompanionConfig
    from src.companion.worker import route_task
    from src.companion.catalog_probe import _iter_records, _gv
    from pywinauto import Desktop
    import win32api
    import win32process
    qc, config = client()
    approval = json.loads((BASE/'write-approval.json').read_text())
    require(approval['run_id'] == config['run_id'] and approval['connection'] == config['connection']
            and approval['confirmed_test_target'] == TARGET, 'Wrong explicit write plan')
    require(not (BASE/'write-started.json').exists(), 'Never rerun an attempted write')
    require(hashlib.sha256(EXE.read_bytes()).hexdigest() == EXPECTED_EXE, 'Installed executable changed')
    require(cdx_reindex.runner_available(), 'Native indexed verification required')
    company, tax = _read_isinfo(Path(TARGET))
    require(company and tax == EXPECTED_TAX, 'TEST identity changed')
    require(not any(_gv(r, 'REFNUM') == approval['invoice'] for r in _iter_records(TARGET, 'APTRN')),
            'Invoice already exists; inspect instead of resending')
    profile_hash = hashlib.sha256(PROFILE.read_bytes()).hexdigest()
    desktop = Desktop(backend='uia')
    original_guard = single_instance.acquire()
    if original_guard is not None:
        original_guard.release()
        raise RuntimeError('Expected installed Companion to own singleton before handoff')
    overflow = desktop.window(class_name='TopLevelWindowForOverflowXamlIsland')
    if not overflow.exists() or not overflow.is_visible():
        desktop.window(class_name='Shell_TrayWnd').child_window(
            title='显示隐藏的图标', control_type='Button').invoke()
    icons = [x for x in overflow.descendants(control_type='Button') if x.window_text().startswith('Pearnly')]
    require(len(icons) == 1 and '空闲' in icons[0].window_text(), 'One idle installed Companion required')
    icons[0].right_click_input()
    menu = desktop.window(class_name='QMenu', title='companion')
    menu.wait('visible', timeout=10)
    handle = win32api.OpenProcess(0x0400 | 0x0010, False, menu.process_id())
    try:
        require(Path(win32process.GetModuleFileNameEx(handle, 0)) == EXE,
                'Quit menu must belong to the verified installed executable')
    finally:
        win32api.CloseHandle(handle)
    # Quit is the deployed cooperative stop: waits for the worker, not process kill.
    quit_item = menu.child_window(title='退出', control_type='MenuItem')
    require(quit_item.exists(), 'Known cooperative Quit action required')
    guard = None
    try:
        WRITE_STATE.update(quit_requested=True, profile_before=profile_hash)
        quit_item.invoke()
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            guard = single_instance.acquire()
            if guard is not None:
                break
            time.sleep(0.5)
        require(guard is not None and guard._win_handle is not None, 'Original singleton was not released')
        WRITE_STATE['singleton_owned'] = True
        require(not account_lock.is_account_busy(TARGET), 'TEST files are busy; do not write')
        # This durable exclusive marker precedes the only poll and all ERP writes.
        with (BASE/'write-started.json').open('x') as stream:
            json.dump(approval, stream)
        task = qc.poll()
        require(task is not None, 'Confirmed TEST task not available')
        payload = task.payload
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'),
            ensure_ascii=False).encode()).hexdigest()
        require(digest == approval['payload_sha256'], 'Leased payload differs from reviewed preparation')
        require(payload['ref_no'] == approval['invoice'] and payload['direction'] == 'purchase'
            and os.path.normcase(payload['account_dir']) == os.path.normcase(TARGET)
            and os.path.normcase(payload['account_set']) == os.path.normcase(TARGET)
            and Decimal(payload['total_amount']) == Decimal('107.00'), 'Wrong single TEST invoice')
        cfg = CompanionConfig(backend_url=config['backend_url'], bearer_token=config['token'],
            method='dbf', account_dir=TARGET, account_set=TARGET, express_root=ROOT,
            authorized_roots=[ROOT], account_company=company, autostart=False, ui_lang='zh')
        WRITE_STATE.update(writer_invoked=True, erp_writes='unknown', wire_task=task.id)
        route_task(cfg, qc, task, index_batch=None)
        WRITE_STATE['route_returned'] = True
        return {'wire_task': task.id, 'route_returned': True}
    finally:
        qc.close()
        if guard is not None:
            guard.release()
        # Restore the deployed executable and its untouched original APPDATA.
        if WRITE_STATE.get('quit_requested'):
            subprocess.Popen([str(EXE), '--background'], cwd=EXE.parent, env=ORIGINAL_ENV,
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
            deadline = time.monotonic() + 30
            restored = False
            while time.monotonic() < deadline:
                check = single_instance.acquire()
                if check is None:
                    restored = True; break
                require(check._win_handle is not None, 'Singleton restoration check failed open')
                check.release(); time.sleep(0.5)
            WRITE_STATE['installed_singleton_restored'] = restored
            WRITE_STATE['profile_unchanged'] = hashlib.sha256(PROFILE.read_bytes()).hexdigest() == profile_hash
            require(restored and WRITE_STATE['profile_unchanged'], 'Installed Companion restoration needs attention')


def readback():
    """Fresh-process, read-only ERP evidence; never infer success from ACK."""
    from src.companion.catalog_probe import _iter_records, _gv
    from src.companion import cdx_reindex, dbf_schema as schema
    approval = json.loads((BASE/'write-approval.json').read_text())
    hits = [{k: _gv(r, k) for k in ('DOCNUM', 'REFNUM', 'NETAMT', 'VATAMT', 'SUPCOD', 'DOCDAT')}
        for r in _iter_records(TARGET, 'APTRN') if _gv(r, 'REFNUM') == approval['invoice']]
    if not hits:
        return {'invoice': approval['invoice'], 'matching_live_headers': 0, 'verified_erp_writes': 0}
    require(len(hits) == 1, 'Expected exactly one live TEST invoice')
    hit = hits[0]; docnum = hit['DOCNUM']
    require(Decimal(hit['NETAMT']) == Decimal('107') and Decimal(hit['VATAMT']) == Decimal('7'), 'ERP header amount differs')
    dr = cr = Decimal(0); rows = 0
    for row in _iter_records(TARGET, 'GLJNLIT'):
        if _gv(row, 'VOUCHER') != docnum:
            continue
        rows += 1; amount = Decimal(_gv(row, 'AMOUNT'))
        if _gv(row, 'TRNTYP') == schema.TRNTYP_DEBIT:
            dr += amount
        else:
            cr += amount
    vats = sum(_gv(row, 'DOCNUM') == docnum for row in _iter_records(TARGET, 'ISVAT'))
    details = [(_gv(row, 'STKCOD'), schema.stcrd_no_stock_impact(row))
        for row in _iter_records(TARGET, 'STCRD') if _gv(row, 'DOCNUM') == docnum]
    require(len(details) == 1 and details[0][1], 'Expected one service line without stock movement')
    masters = [(_gv(row, 'STKCOD'), _gv(row, 'STKTYP')) for row in _iter_records(TARGET, 'STMAS')
        if _gv(row, 'STKCOD') == details[0][0]]
    require(len(masters) == 1 and masters[0][1] != '0', 'Service master missing or marked as stock')
    indexed = cdx_reindex.seek(Path(TARGET), 'APTRN', 'APTRN1', docnum)
    require(rows >= 2 and dr == cr == Decimal('107') and vats == 1
            and indexed.found and indexed.docnum == docnum, 'ERP journals, tax or indexed readback differs')
    return {'invoice': approval['invoice'], 'matching_live_headers': 1, 'verified_erp_writes': 1,
        'erp_docnum': docnum, 'net': hit['NETAMT'], 'vat': hit['VATAMT'],
        'journal_rows': rows, 'debit': str(dr), 'credit': str(cr), 'vat_rows': vats,
        'indexed_seek': True, 'detail_rows': len(details), 'no_stock_movement': True,
        'service_master': masters[0][0], 'service_master_type': masters[0][1]}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['inventory', 'collect', 'report', 'poll-empty', 'write-once', 'readback'])
    args = parser.parse_args()
    output = {'captured_at': datetime.now(timezone.utc).isoformat(), 'action': args.action,
              'erp_writes': 0, 'profile_changes': 0}
    try:
        verify_bundle()
        output.update(result={'inventory': inventory, 'collect': collect, 'report': report,
                              'poll-empty': poll_empty, 'write-once': write_once, 'readback': readback}[args.action](), status='ok')
    except Exception as error:
        # Do not include request URLs/headers, tokens, payloads or complete DBF rows.
        output.update(status='failed', error_type=type(error).__name__)
        if isinstance(error, RuntimeError):
            output['reason'] = str(error)
    if args.action == 'write-once':
        output.update(WRITE_STATE)
    (BASE/(args.action + '-result.json')).write_text(json.dumps(output, indent=2), encoding='utf-8')
    raise SystemExit(0 if output['status'] == 'ok' else 1)
