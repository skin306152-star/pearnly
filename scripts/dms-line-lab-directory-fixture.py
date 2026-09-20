"""Explicit native CRUD of owned test directory entries, never existing records.

No orders, payments or messages. Writes require --execute-lab-writes; a durable
journal prevents replay after an unknown response. Passwords stay in memory.
"""
import argparse
import json
import os
import secrets
from pathlib import Path

import frappe
from bs4 import BeautifulSoup

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('phase', choices=('create', 'rename', 'disable-bank', 'reparent', 'delete', 'read'))
parser.add_argument('--execute-lab-writes', action='store_true')
parser.add_argument('--family', choices=('directory', 'geo'), default='directory')
args = parser.parse_args()
if args.phase != 'read' and not args.execute_lab_writes:
    parser.error('Explicit external test writes must be selected')
SITE = 'dms-line-lab.localhost'
MARKER = 'E2E260920M'
KINDS = ('prefix', 'bank', 'branch', 'team', 'users')
MENUS = {'prefix': ('20', '2'), 'bank': ('31', '3'), 'branch': ('22', '3'),
         'team': ('23', '3'), 'users': ('1', '2')}
CODES = {'prefix': 'txtprefixname', 'bank': 'txtbankcode', 'branch': 'txtbranchcode',
         'team': 'txtteamcode', 'users': 'txtuserscode'}
NAMES = {'prefix': 'txtprefixname', 'bank': 'txtbankname', 'branch': 'txtbranchname',
         'team': 'txtteamname', 'users': 'txtusersname'}
if args.family == 'geo':
    assert args.phase != 'disable-bank', 'Native address masters do not have a disable control'
    MARKER = 'E2E260920G'
    KINDS = ('provinces', 'districts', 'subdistricts', 'zipcodes')
    MENUS = {kind: (str(index + 27), '3') for index, kind in enumerate(KINDS)}
    CODES = NAMES = {kind: 'txt' + kind + 'name' for kind in KINDS}
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site=SITE, sites_path='.')
frappe.connect()
frappe.set_user('Administrator')
from pearnly_dms.adapter.session import MrerpDmsAdapter

private = Path(SITE) / 'private'
path = private / ('line-lab-' + args.family + '-fixture.json')
cfg = json.loads(Path('/lab-private/external-fixture.json').read_text())
assert cfg['source_site'] == 'testdms.pearnly.com'
ep = cfg['endpoint']
journal = json.loads(path.read_text()) if path.exists() else {'marker': MARKER, 'steps': [], 'ids': {}}
assert journal['marker'] == MARKER


def persist():
    path.write_text(json.dumps(journal, ensure_ascii=False, indent=2))
    path.chmod(0o600)


def initialize(client, kind):
    menu, level = MENUS[kind]
    client._post_text(kind + '/view.php', {'idmenu': menu, 'menulv': level})


def form(client, kind, status='n', record_id=''):
    initialize(client, kind)
    markup = client._post_text(kind + '/form.php', {'status': status, 'id': record_id})
    soup = BeautifulSoup(markup, 'html.parser')
    values = {}
    for tag in soup.select('form input[name],form select[name],form textarea[name]'):
        if tag.get('type') in ('file', 'button', 'submit', 'reset'):
            continue
        if tag.get('type') in ('checkbox', 'radio') and not tag.has_attr('checked'):
            continue
        value = tag.get('value', '')
        if tag.name == 'select':
            option = tag.select_one('option[selected]') or tag.select_one('option')
            value = option.get('value', '') if option else ''
        if tag.name == 'textarea':
            value = tag.get_text()
        key = tag['name']
        if key.endswith('[]'):
            values.setdefault(key, []).append(value)
        else:
            values[key] = value
    assert values.get('stsel') == status
    return values


def rows(client, kind):
    initialize(client, kind)
    markup = client._post_text(kind + '/component/showdata.php', {
        'sdtamt': '100', 'sdtpage': '1', 'sd': MARKER, 'ftd': '1',
        'selcolsort': '1', 'selcolsorttype': '1',
    })
    assert markup.lstrip().startswith(('dt::', 'ndt::')), kind
    return [{'id': tag['data-val'], 'text': tag.get_text(' ', strip=True)}
            for tag in BeautifulSoup(markup, 'html.parser').select('[data-val]')
            if MARKER in tag.get_text()]


def mutate(client, kind, operation, values):
    if kind == 'zipcodes':
        assert values[CODES[kind]] in ('99998', '99997')
        assert values['selsubdistricts'] == journal['ids']['subdistricts']
    else:
        assert str(values[CODES[kind]]).startswith(MARKER), 'Only owned fixtures may change'
    if operation != 'new':
        assert str(values['idsel']) == journal['ids'][kind]
    assert not any(step['state'] == 'sent' for step in journal['steps']), 'Reconcile unknown result first'
    event = {'kind': kind, 'operation': operation, 'state': 'sent', 'id': values.get('idsel')}
    journal['steps'].append(event)
    persist()
    encoded = {}
    for key, value in values.items():
        if isinstance(value, list):
            for index, item in enumerate(value):
                encoded[f'{key[:-2]}[{index}]'] = item
        else:
            encoded[key] = value
    response = client._post_text(kind + '/' + operation + '.php', encoded)
    event.update(state='response', response=response[:1000])
    persist()
    assert not response.lstrip().startswith('err::'), (kind, operation, response[:300])


def create_values(client, kind):
    values = form(client, kind)
    values[CODES[kind]] = MARKER
    values[NAMES[kind]] = MARKER + ' test'
    if args.family == 'geo':
        for parent in ('provinces', 'districts', 'subdistricts'):
            if 'sel' + parent in values:
                values['sel' + parent] = journal['ids'][parent]
        if kind == 'zipcodes':
            values[NAMES[kind]] = '99998'
        elif 'txt' + kind + 'engname' in values:
            values['txt' + kind + 'engname'] = MARKER + ' test'
        return values
    if kind in ('bank', 'branch', 'users'):
        qa = json.loads((private / 'line-lab-fields-expected.json').read_text())
        draft = qa['draft']
        for field, key in [('selprovinces', 'province_id'), ('seldistricts', 'district_id'),
                           ('selsubdistricts', 'subdistrict_id'), ('selzipcodes', 'zipcode_id')]:
            values[field] = draft[key]
        values.update(txthousenum='920 TEST', txttel='0990000923', txtemail='', txttaxid='1000009200019')
    if kind == 'bank':
        values.update(txtbranchname=MARKER + ' test branch', txtaccountnum='0000920260')
    elif kind == 'branch':
        values.update(selcompany='1', txtbranchautonum='E2ETEST')
    elif kind == 'team':
        values['selbranch'] = journal['ids']['branch']
    elif kind == 'users':
        values.update(txtusers=MARKER.lower() + '_login', txtpasswords=secrets.token_urlsafe(24),
                      selposition='6', selbranch=journal['ids']['branch'],
                      chkteamrdo=journal['ids']['team'])
    return values


try:
    with MrerpDmsAdapter(system_url=ep['system_url'], username=ep['admin_username'],
                         password=ep['admin_password']) as adapter:
        adapter.login()
        client = adapter.client()
        if args.phase == 'create':
            assert not journal['steps'], 'Creation cannot replay an existing journal'
            assert all(not rows(client, kind) for kind in KINDS)
            for kind in KINDS:
                mutate(client, kind, 'new', create_values(client, kind))
                found = rows(client, kind)
                assert len(found) == 1, kind
                journal['ids'][kind] = found[0]['id']
                persist()
        elif args.phase == 'rename':
            for kind in KINDS:
                values = form(client, kind, 'e', journal['ids'][kind])
                expected = '99997' if kind == 'zipcodes' else MARKER + ' renamed'
                values[NAMES[kind]] = expected
                if kind == 'bank':
                    values.update(txtaccountnum='0000920261', txtbranchname=MARKER + ' new branch')
                mutate(client, kind, 'edit', values)
                assert form(client, kind, 'v', journal['ids'][kind])[NAMES[kind]] == expected
        elif args.phase == 'disable-bank':
            values = form(client, 'bank', 'e', journal['ids']['bank'])
            assert 'selstatuscancel' in values
            values['selstatuscancel'] = '1'
            mutate(client, 'bank', 'edit', values)
            assert form(client, 'bank', 'v', journal['ids']['bank'])['selstatuscancel'] == '1'
        elif args.phase == 'reparent':
            assert args.family == 'geo'
            values = form(client, 'districts', 'e', journal['ids']['districts'])
            qa = json.loads((private / 'line-lab-fields-expected.json').read_text())
            values['selprovinces'] = qa['draft']['province_id']
            mutate(client, 'districts', 'edit', values)
            assert form(client, 'districts', 'v', journal['ids']['districts'])['selprovinces'] == values['selprovinces']
        elif args.phase == 'delete':
            for kind in reversed(KINDS):
                found = rows(client, kind)
                if not found:
                    assert any(s['kind'] == kind and s['operation'] == 'del' for s in journal['steps'])
                    continue
                assert len(found) == 1 and found[0]['id'] == journal['ids'][kind]
                values = form(client, kind, 'd', journal['ids'][kind])
                mutate(client, kind, 'del', values)
                assert not rows(client, kind), 'Deletion must be independently read back'
        result = {kind: rows(client, kind) for kind in KINDS}
        journal.setdefault('readbacks', []).append({'phase': args.phase, 'result': result})
        persist()
        print(json.dumps({'phase': args.phase, 'owned_directories': result}, ensure_ascii=False))
finally:
    frappe.destroy()
