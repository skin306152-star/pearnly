"""Real MariaDB first-session contention, CAS and submitted-write protection.

Local test Site only; no messages, provider calls or external writes. Removes only
its unique prefixed sessions in finally.
"""
import os
import secrets
from concurrent.futures import ThreadPoolExecutor
import frappe
from pearnly_core import line_session as sessions
os.chdir('/home/frappe/frappe-bench/sites')
site='dms-line-lab-b.localhost'
prefix='Ulab-session-capacity-'+secrets.token_hex(5)+'-'

def connect():
    frappe.init(site=site,sites_path='.')
    frappe.connect();frappe.set_user('Administrator')
    assert frappe.conf.pearnly_line_channel_access_token=='lab:'+site

def participant(index):
    connect()
    try:
        uid=prefix+str(index)
        sessions.set_session(uid,'reviewing',{'nonce':str(index)})
        assert sessions.get_session(uid)['payload']['nonce']==str(index)
        return True
    finally: frappe.destroy()

try:
    with ThreadPoolExecutor(max_workers=20) as pool:
        assert all(pool.map(participant,range(100)))
    print('PASS 100 first sessions / 20 concurrent / no gap-lock deadlock',flush=True)
    connect()
    uid=prefix+'guard'
    sessions.set_session(uid,'booking_review',{'nonce':None,'booking_claim':'n','booking_attempt':{'nonce':'n'}})
    try: sessions.set_session(uid,'collecting',{})
    except RuntimeError: frappe.db.rollback()
    else: raise AssertionError('Pending booking was replaced')
    assert sessions.get_session(uid)['payload']['booking_attempt']['nonce']=='n'
    print('PASS unresolved external booking remains protected',flush=True)
    frappe.destroy()
finally:
    connect()
    frappe.db.delete(sessions.DT,{'line_user_id':['like',prefix+'%']})
    frappe.db.commit();frappe.destroy()
