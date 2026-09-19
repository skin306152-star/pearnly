"""Native MIME/escaping/redaction check; rollback-only, SMTP prohibited.
Run in an existing Frappe test/maintenance runtime with a candidate package path.
"""
import csv
from html.parser import HTMLParser
from email import policy
from email.parser import Parser
from pathlib import Path
import sys
from unittest.mock import patch

import frappe
from frappe.email.doctype.email_account.email_account import EmailAccount

package = Path(sys.argv[1])
frappe.init(site='control.localhost',sites_path='/home/frappe/frappe-bench/sites')
frappe.connect();frappe.set_user('Administrator')
before=frappe.db.count('Email Queue')
try:
    account=frappe.get_doc({'doctype':'Email Account','name':'Pearnly','email_account_name':'Pearnly',
        'email_id':'hello@pearnly.com','enable_outgoing':1,'always_use_account_email_id_as_sender':1,
        'always_use_account_name_as_sender_name':1,'track_email_status':0,'add_reply_to_header':1,'reply_to_addresses':[{'email':'hello@pearnly.com','_name':'Pearnly'}]})
    template=(package/'templates/emails/pearnly_welcome.html').read_text()
    with patch.object(EmailAccount,'find_outgoing',return_value=account), \
         patch('smtplib.SMTP',side_effect=AssertionError('SMTP prohibited')), \
         patch('smtplib.SMTP_SSL',side_effect=AssertionError('SMTP prohibited')), \
         patch.object(frappe.db,'commit'):
        for lang in ('zh','th','en','ja'):
            with (package/f'translations/{lang}.csv').open() as f:translations=dict(list(csv.reader(f)))
            tr=lambda s:translations.get(s,s)
            html=frappe.render_template(template,{'language':lang,'company':'<script>bad</script>',
                'username':'example','email':'example@example.invalid','password':'Secret<&>123',
                'product_name':'Pearnly DMS','products':[{'name':'Pearnly DMS',
                    'domain':'company.example.com','url':'https://company.example.com/'}],'t':tr},is_path=False)
            assert '<script>bad</script>' not in html and '&lt;script&gt;' in html
            assert 'Secret&lt;&amp;&gt;123' in html
            assert '租户' not in html and '专属空间' not in html and '已开通功能' not in html
            assert 'border-left:3px' not in html and 'border-top:5px' not in html
            q=frappe.sendmail(recipients=['example@example.invalid'],sender='Pearnly <hello@pearnly.com>',
                reply_to='hello@pearnly.com',subject=tr('Welcome to {0} — your account is ready').format('Pearnly DMS'),
                message=html,raw_html=True,add_css=False,delayed=True,add_unsubscribe_link=0,
                redact_message_after_send=True,inline_images=[{'filename':'mail-logo.png',
                    'filecontent':(package/'public/images/mail-logo.png').read_bytes()}])
            assert q and q.status=='Not Sent'
            mime=Parser(policy=policy.default).parsestr(q.message)
            assert str(mime['From'])=='Pearnly <hello@pearnly.com>', str(mime['From'])
            assert 'hello@pearnly.com' in str(mime['Reply-To'])
            assert mime['Bcc'] is None
            assert any(p.get_content_type()=='image/png' for p in mime.walk())
            html_body=mime.get_body(preferencelist=('html',)).get_content()
            class Images(HTMLParser):
                def handle_starttag(self, tag, attrs):
                    if tag == 'img':
                        sources=[v for k,v in attrs if k=='src']
                        assert len(sources)==1 and sources[0].startswith('cid:'), 'Image must have exactly one CID source'
                        assert dict(attrs).get('height')=='54', 'Reserve the logo height in email clients'
            Images().feed(html_body)
            q.redact_message();q.reload()
            assert 'Secret' not in q.message and 'company.example.com' not in q.message
        print('PASS: four languages, escaped credentials, official headers, CID logo, native queue and redaction; no SMTP')
finally:
    frappe.db.rollback()
    assert frappe.db.count('Email Queue')==before,'Test queue leaked'
    frappe.destroy()
