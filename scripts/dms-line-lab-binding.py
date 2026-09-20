"""Local MariaDB/Redis short-code tests; never binds or contacts a real LINE user."""
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import hashlib
import json
import os
import uuid

import frappe

os.chdir('/home/frappe/frappe-bench/sites')
SITE = 'dms-line-lab-b.localhost'
frappe.init(site=SITE, sites_path='.')
frappe.connect()
frappe.set_user('Administrator')
assert frappe.conf.pearnly_line_channel_access_token == 'lab:' + SITE
from pearnly_core import line_binding as binding

prefix = 'binding-check-' + uuid.uuid4().hex[:8]
users = []
actors = []
checks = []


def check(label, actual):
    assert actual, label
    checks.append(label)


def actor():
    name = prefix + '-' + str(len(actors))
    actors.append(name)
    return name


def consume(code, sender):
    try:
        return binding.consume_bind_code(code, line_user_id=sender)
    finally:
        frappe.db.rollback()


def concurrent(function, values):
    def run(value):
        frappe.init(site=SITE, sites_path='.')
        frappe.connect()
        frappe.set_user('Administrator')
        try:
            return function(value)
        finally:
            frappe.db.rollback()
            frappe.destroy()
    frappe.db.commit()
    with ThreadPoolExecutor(20) as pool:
        results = list(pool.map(run, values))
    frappe.db.rollback()
    return results


try:
    for index in range(20):
        name = prefix + '-' + str(index) + '@example.test'
        frappe.get_doc(dict(doctype='User', email=name, first_name=prefix, user_type='Website User',
                            send_welcome_email=0)).insert(ignore_permissions=True)
        users.append(name)
    frappe.db.commit()
    with patch.object(binding.secrets, 'choice', side_effect=list('001237')):
        first = binding.generate_bind_code(users[0])
    check('leading zero preserved', first['code'] == '001237')
    with patch.object(binding.secrets, 'choice', side_effect=list('001237009876')):
        second = binding.generate_bind_code(users[1])
    check('primary key collision retried without losing other code', second['code'] == '009876'
          and frappe.db.get_value(binding.BIND_CODE, first['code'], 'user') == users[0])
    check('TTL in seconds', second['expires_in'] == 1800)
    fresh = binding.generate_bind_code(users[0])
    check('regeneration revokes previous code', consume(first['code'], actor()) is None)
    sender = actor()
    results = concurrent(lambda _: consume(fresh['code'], sender), range(20))
    check('20 simultaneous consumers only one succeeds', sum(bool(value) for value in results) == 1)
    codes = concurrent(lambda user: binding.generate_bind_code(user)['code'], users)
    check('20 independent users unique six digit codes', len(set(codes)) == 20
          and all(len(code) == 6 and code.isdigit() for code in codes))
    codes = concurrent(lambda _: binding.generate_bind_code(users[2])['code'], range(20))
    check('same user regeneration leaves one active code', frappe.db.count(binding.BIND_CODE,
          {'user': users[2], 'used': 0}) == 1)
    active = frappe.db.get_value(binding.BIND_CODE, {'user': users[2], 'used': 0}, 'name')
    sender = actor()
    for _ in range(5):
        consume('ABC123', sender)
    check('guessing budget enforced', consume(active, sender) is None)
    check('another legitimate sender not locked out', bool(consume(active, actor())))
    expired = binding.generate_bind_code(users[3], ttl_minutes=-1)
    check('expired code rejected', consume(expired['code'], actor()) is None)
    check('anonymous consumption rejected', consume(second['code'], '') is None)
    check('used code cannot be consumed twice', consume(fresh['code'], actor()) is None)
    disabled = binding.generate_bind_code(users[4])
    frappe.db.set_value('User', users[4], 'enabled', 0)
    frappe.db.commit()
    check('disabled operator rejected', consume(disabled['code'], actor()) is None)
finally:
    frappe.db.rollback()
    if users:
        frappe.db.delete(binding.BIND_CODE, {'user': ['in', users]})
        for user in users:
            for attempt in range(5):
                try:
                    frappe.delete_doc('User', user, ignore_permissions=True, force=True)
                    frappe.db.commit()
                    break
                except frappe.QueryDeadlockError:
                    frappe.db.rollback()
                    if attempt == 4:
                        raise
        frappe.db.commit()
    for sender in actors:
        frappe.cache.delete_value('pearnly_bind_attempts:' + hashlib.sha256(sender.encode()).hexdigest())
print(json.dumps({'passed': len(checks), 'checks': checks, 'synthetic_users_removed': len(users)}))
