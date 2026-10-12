"""Orchestration regression tests in the pinned Frappe runtime; no external IO."""
import copy
from contextlib import contextmanager, nullcontext
import unittest
from unittest.mock import Mock, patch

import frappe
import requests
from pearnly_core import line, ocr
from pearnly_dms.booking import service, booking_ledger
from pearnly_dms.booking.booking_org import BookingOrganization
from pearnly_dms.contracts.models import DMSMasterRef
from pearnly_dms.intake import service as intake_service
from pearnly_dms.contracts.client_base import DmsClientError


class AttachmentProtocol(unittest.TestCase):
    def test_second_file_uses_updated_form_and_retry_does_not_duplicate(self):
        from pearnly_dms.client import attachments
        form = {'stsel': 'e', 'idsel': '1', 'txtdocno': 'LOCAL', 'txtcusname': 'preserve'}
        client = Mock()
        client._parse_form_defaults.side_effect = lambda raw: copy.deepcopy(form)
        uploads = []
        def upload(*args, **kwargs):
            fields = kwargs['data']
            if uploads:
                self.assertEqual(fields['fulcurrname1'], 'id card')
                self.assertEqual(fields['fulcurr1'], 'first-file-id')
            uploads.append(fields['fulnewname[]'])
            form[f'fulcurrname{len(uploads)}'] = uploads[-1]
            form[f'fulcurr{len(uploads)}'] = 'first-file-id'
            # Simulate a timeout after the remote side has already committed.
            raise requests.Timeout('after commit')
        client.transport.post.side_effect = upload
        files = [{'display_name': label, 'filename': 'test.jpg', 'content_type': 'image/jpeg', 'content': b'test'}
                 for label in ['id card', 'test slip']]
        self.assertEqual(attachments.attach(client, booking_id='1', files=files),
                         {'ok': True, 'attached': 2, 'failed': []})
        self.assertTrue(attachments.attach(client, booking_id='1', files=files)['ok'])
        self.assertEqual(uploads, ['id card', 'test slip'])

    def test_lost_old_attachment_and_wrong_document_are_rejected(self):
        from pearnly_dms.client import attachments
        client = Mock()
        before = {'stsel': 'e', 'idsel': '1', 'txtdocno': 'LOCAL', 'fulcurrname1': 'old'}
        after = {'stsel': 'e', 'idsel': '1', 'txtdocno': 'LOCAL', 'fulcurrname1': 'new'}
        client._parse_form_defaults.side_effect = [before, after]
        files = [{'display_name': 'new', 'filename': 'test.jpg', 'content_type': 'image/jpeg', 'content': b'test'}]
        self.assertFalse(attachments.attach(client, booking_id='1', files=files)['ok'])
        client.reset_mock()
        client._parse_form_defaults.side_effect = [{**before, 'idsel': 'another'}]
        self.assertFalse(attachments.attach(client, booking_id='1', files=files)['ok'])
        client.transport.post.assert_not_called()


class RichMenuIsolation(unittest.TestCase):
    def test_each_site_uses_its_own_menu_lock_and_repeated_sync_is_deduplicated(self):
        from pearnly_dms.menu import rich_menu
        names = []
        for site in ('tenant-a', 'tenant-b'):
            cache = Mock()
            cache.make_key.side_effect = lambda key, site=site: site + ':' + key
            cache.lock.return_value = nullcontext()
            with patch.object(frappe, 'conf', new={rich_menu.CONFIG: {'basic': 'basic', 'query': 'query'}}), \
                 patch.object(frappe, 'cache', return_value=cache), \
                 patch.object(rich_menu.line_binding, 'binding_of', return_value=None), \
                 patch.object(rich_menu, '_request') as request, patch.object(frappe, 'enqueue') as enqueue:
                rich_menu.sync('same-line-user')
                names.append(cache.lock.call_args.args[0])
                request.assert_called_once_with('DELETE', 'user/same-line-user/richmenu', missing_ok=True)
                rich_menu.request_sync('same-line-user')
                first = enqueue.call_args.kwargs
                rich_menu.request_sync('same-line-user')
                self.assertEqual(first['job_id'], enqueue.call_args.kwargs['job_id'])
                self.assertTrue(first['deduplicate'] and first['enqueue_after_commit'])
        self.assertNotEqual(*names)


class RecoveryContract(unittest.TestCase):
    def test_unrecoverable_first_page_does_not_starve_later_attempts(self):
        from pearnly_dms.booking import recovery
        cache = Mock()
        rows = [frappe._dict(name=f'{i:03}', line_user_id=f'U{i}', payload='{}') for i in range(50)]
        with patch.object(frappe, 'cache', return_value=cache), \
             patch.object(frappe, 'db', new=Mock()) as db, patch.object(recovery, 'request') as dispatch, \
             patch.object(frappe, 'get_all', return_value=[]) as held, \
             patch.object(frappe.utils, 'now', return_value='2026-10-13 09:00:00.000000'):
            cache.get_value.return_value = ''
            db.sql.return_value = rows
            dispatch.side_effect = RuntimeError('one corrupt/unqueuable attempt must not end scan')
            with patch.object(recovery.logger, 'warning'):
                recovery.reconcile_due()
            self.assertEqual(dispatch.call_count, 50)
            self.assertEqual(cache.set_value.call_args.args[1], '049')
            cache.get_value.return_value = '049'
            db.sql.return_value = []
            recovery.reconcile_due()
            self.assertEqual(db.sql.call_args.args[1], ('049',))
            self.assertEqual(cache.set_value.call_args.args[1], '')
            # Held unknown results are checked from their own durable records, due ones only.
            self.assertEqual(held.call_args.args[0], 'DMS Booking Delivery')
            self.assertEqual(held.call_args.kwargs['filters']['status'], 'Unverified')

    def test_exact_readback_contract_is_recorded_before_external_post(self):
        from pearnly_dms.booking import booking_submit
        events = []
        recorder = Mock()
        recorder.capture_submission.side_effect = lambda number, fields: events.append(('capture', number, fields))
        client = Mock()
        client._url.return_value = 'https://test.invalid/drfcbc/new.php'
        client.transport.post.side_effect = lambda *a, **kw: (
            events.append(('POST',)), Mock(status_code=200, text='ok'))[1]
        with patch.object(booking_submit, 'verify_created_booking', return_value='1'):
            result = booking_submit.submit_booking(client, {'cusval': '127', 'txttel': 'local',
                'irrelevant_secret': 'must-not-persist'}, 'LOCAL-ONLY', on_attempt=recorder)
        self.assertEqual(result, ('1', 'LOCAL-ONLY'))
        self.assertEqual(events[0], ('capture', 'LOCAL-ONLY', {'cusval': '127', 'txttel': 'local', 'txtdocno': 'LOCAL-ONLY'}))
        self.assertEqual(events[1], ('POST',))
        recorder.assert_not_called()

    def test_failed_evidence_storage_prevents_external_post(self):
        from pearnly_dms.booking import booking_submit
        recorder = Mock()
        recorder.capture_submission.side_effect = RuntimeError('storage unavailable')
        client = Mock()
        with self.assertRaises(RuntimeError):
            booking_submit.submit_booking(client, {'cusval': '127'}, 'LOCAL-ONLY', on_attempt=recorder)
        client.transport.post.assert_not_called()

    def test_only_line_accepted_retry_conflict_is_success(self):
        response = Mock(status_code=409, headers={'x-line-accepted-request-id': 'accepted'}, text='conflict')
        with patch.object(line, 'mirroring', return_value=False), patch.object(line, '_headers', return_value={}), \
             patch.object(line.requests, 'post', return_value=response) as post, patch.object(frappe, 'log_error'):
            self.assertTrue(line.push('U-local', [{'type': 'text', 'text': 'fixture'}], retry_key='same-key'))
            self.assertEqual(post.call_args.kwargs['headers']['X-Line-Retry-Key'], 'same-key')
            response.headers = {}
            self.assertFalse(line.push('U-local', [], retry_key='same-key'))


class NativeModelStatus(unittest.TestCase):
    def test_cancelled_company_account_is_not_a_valid_transfer_destination(self):
        from pearnly_dms.masters.company_banks import fetch_company_banks
        client = Mock()
        client._bshsd_all.return_value = [[1, 'bank', 'same', 'branch', '111'], [2, 'bank', 'same', 'branch', '222']]
        client._post_text.side_effect = ['native view', '''
            <div onclick="ctllistdata(this);" data-val="1"><p>-</p></div>
            <div onclick="ctllistdata(this);" data-val="2"><p>ยกเลิก</p></div>''']
        self.assertEqual([row[0] for row in fetch_company_banks(Mock(), client=client)], ['1'])
        self.assertEqual(client._post_text.call_args_list[0].args[0], 'bank/view.php')

    def test_cancelled_picker_row_is_excluded_by_stable_id(self):
        from pearnly_dms.client.master_status import active_models
        client = Mock()
        client._post_text.side_effect = ["native view", '''
            <div onclick="ctllistdata(this);" data-val="1"><div><p>-</p><p>same name</p></div></div>
            <div onclick="ctllistdata(this);" data-val="2"><div><p>ยกเลิก</p><p>same name</p></div></div>''']
        self.assertEqual(active_models(client, [[1, "A", "same name"], [2, "B", "same name"]]),
                         [[1, "A", "same name"]])

    def test_missing_unknown_and_repeating_pages_are_not_empty_success(self):
        from pearnly_dms.client.master_status import active_models
        row = '<div onclick="ctllistdata(this);" data-val="1"><p>-</p></div>'
        for pages, kwargs in ((["empty"], {}), ([row.replace("<p>-", "<p>unknown")], {}),
                              ([row, row], {"page_size": 1})):
            client = Mock()
            client._post_text.side_effect = ["native view", *pages]
            with self.assertRaises(DmsClientError):
                active_models(client, [[1, "A", "name"]], **kwargs)


class EmployeeIdentity(unittest.TestCase):
    def test_employee_paging_must_be_complete_and_unique(self):
        from pearnly_dms.masters import employees
        def row(i):
            return f'<div data-val="{i}"><p>CODE-{i}</p><p>login-{i}</p><p>Name {i}</p></div>'
        client = Mock()
        client._post_text.side_effect = [row(1), row(2), 'ndt::']
        with patch.dict(employees._LIST_BODY, {'sdtamt': '1'}):
            self.assertEqual([r['login'] for r in employees._fetch_once(client)], ['login-1', 'login-2'])
            client._post_text.side_effect = [row(1), row(1)]
            self.assertIsNone(employees._fetch_once(client))
        client._post_text.side_effect = ['']
        self.assertIsNone(employees._fetch_once(client))


class EditedCustomer(unittest.TestCase):
    def test_buddhist_leap_dates_use_gregorian_leap_year(self):
        from pearnly_dms.contracts.client_base import parse_be_date
        self.assertEqual(parse_be_date("29/02/2567").isoformat(), "2024-02-29")
        for value in ("29/02/2568", "31/04/2569", "1/1/2569", "2026-09-20"):
            with self.assertRaises(ValueError):
                parse_be_date(value)

    def test_reopening_explicitly_edited_customer_keeps_cleared_fields(self):
        from pearnly_dms.editor import booking_edit as editor, customer_edit
        from pearnly_dms.editor.edit_snapshot import _merge_customer_defaults
        customer = {"people_id": "1000000730317", "building": "", "road": "", "name": "Edited"}
        def snapshot(endpoint, *, customer, customer_id="", **kwargs):
            old = {"building": "Old building", "road": "Old road"} if customer_id else {}
            return {"resolved_customer": _merge_customer_defaults(customer, old), "prefixes": [],
                    "masters": {}, "paints": {}, "geo": {}}
        qa = {"customer": {"id": "127"}, "draft": customer, "customer_edited": True}
        # load 会把补齐后的复核资料按票据写回；这部分由 CAS 保护，单元测试里
        # 没有真实会话/nonce，打桩成成功，测试只关心已清空字段是否被保留。
        with patch.object(editor, "_review", return_value=({}, {"qa": qa}, "EP-own")), \
             patch.object(editor, "_snapshot", side_effect=snapshot), \
             patch.object(editor, "_masters", return_value={}), \
             patch.object(editor.store, "replace_review_payload", return_value=True):
            form = editor.load("U", "nonce")["form"]["customer"]
            self.assertEqual((form["building"], form["road"], form["people_id"]), ("", "", customer["people_id"]))
        payload = {"draft": customer, "customer_id": "127", "customer_edited": True}
        with patch.object(customer_edit, "_review", return_value=({}, payload, "EP-own")), \
             patch.object(editor, "_snapshot", side_effect=snapshot):
            form = customer_edit.load("U", "nonce")["form"]["customer"]
            self.assertEqual((form["building"], form["road"]), ("", ""))


class MasterRepair(unittest.TestCase):
    def setUp(self):
        self.qa = {"answers": {"place": {"id": "p"}, "car": {"id": "c"},
                   "paint": {"id": "color"}, "delivery_date_be": "21/10/2569",
                   "term": {"id": "t"}, "regis": {"id": "r"}, "regis_name": "keep me"},
                   "payments": [{"channel": "cash", "amount": "29.00"}],
                   "customer": {"id": "127"}, "files": {"id_card_mid": "image"}}

    def test_invalid_master_only_clears_its_dependents(self):
        from pearnly_dms.masters.master_contract import _missing_result
        for field in ("place", "car", "paint", "term", "regis"):
            fixed = _missing_result(self.qa, field, {}, [])["qa"]
            removed = {"car", "paint"} if field == "car" else {field}
            self.assertEqual(set(self.qa["answers"]) - set(fixed["answers"]), removed)
            for key in ("customer", "payments", "files"):
                self.assertEqual(fixed[key], self.qa[key])
            self.assertEqual(fixed["answers"]["delivery_date_be"], "21/10/2569")
            self.assertEqual(fixed["answers"]["regis_name"], "keep me")

    def test_repair_reasks_color_then_new_review_not_the_whole_form(self):
        from pearnly_dms.booking.conversation import booking_qa
        qa = copy.deepcopy(self.qa)
        qa["repair_fields"] = ["car", "paint"]
        with patch.object(booking_qa, "_persist"), patch.object(booking_qa, "send_step") as send, \
             patch.object(booking_qa, "_to_preview") as preview:
            booking_qa._advance_answer("site", "U", qa, "car", "paint", "reply")
            self.assertEqual(qa["repair_fields"], ["paint"])
            self.assertEqual(send.call_args.args[3], "paint")
            preview.assert_not_called()
            booking_qa._advance_answer("site", "U", qa, "paint", "date", "reply")
            preview.assert_called_once()
            self.assertNotIn("repair_fields", qa)
            self.assertEqual(qa["payments"], self.qa["payments"])

    def test_pending_write_blocks_restart_image_and_postback_before_routing(self):
        from pearnly_dms.line import handlers
        from pearnly_core import line_session
        from pearnly_dms.booking import booking_attempt
        events = [{"type": "message", "message": {"type": "text", "text": "เริ่มใหม่"}},
                  {"type": "message", "message": {"type": "image", "id": "image"}},
                  {"type": "postback", "postback": {"data": "menu:2"}}]
        marker = {"booking_no": "LOCAL-ONLY"}
        with patch.object(handlers.line_binding, "user_of", return_value="employee"), \
             patch.object(line_session, "get_session", return_value={"payload": {"booking_attempt": marker}}), \
             patch.object(booking_attempt, "message", return_value="Checking the existing submission"), \
             patch.object(line, "reply") as reply, patch.object(handlers, "_handle_text") as route:
            for event in events:
                self.assertTrue(handlers.handle({**event, "source": {"userId": "U"}, "replyToken": "r"}))
            self.assertEqual(reply.call_count, 3)
            route.assert_not_called()

    def test_conversation_binding_carries_the_live_revision(self):
        """菜单 5 的「读取／看结果」要求绑定版本非空且仍是当前绑定。

        2026-09-25 实测：对话上下文的 binding 少了 `binding_revision`，那两个按钮
        一律被判成"没有权限"（操作员只收到"未获授权搜索"）。这里守住这个键必须
        来自**当前**绑定行，而不是调用方手里的旧值。
        """
        from pearnly_dms.line import handlers, identity
        live = {"user": "employee", "binding_revision": "rev-9"}
        with patch.object(frappe.local, "site", "offline-line.test", create=True), \
             patch.object(identity, "current", return_value=live):
            binding = handlers._binding("U-lab", "employee")
        self.assertEqual(binding["user_id"], "employee")
        self.assertEqual(binding["line_user_id"], "U-lab")
        self.assertEqual(binding["binding_revision"], "rev-9")
        with patch.object(frappe.local, "site", "offline-line.test", create=True), \
             patch.object(identity, "current", return_value=None):
            self.assertEqual(handlers._binding("U-lab", "employee")["binding_revision"], "",
                             "没有当前绑定时必须留空，不能编一个版本出来")


class LineIdentity(unittest.TestCase):
    def setUp(self):
        self.config = {"pearnly_line_channel_id": "2222222222", "pearnly_line_liff_id": "1111111111-app"}
        self.config["pearnly_line_channel_access_token"] = "lab-token"
        self.enterContext(patch.object(frappe, "conf", self.config))
        self.post = self.enterContext(patch.object(line.requests, "post"))
        self.post.return_value.status_code = 200
        self.claims = {"aud": "1111111111", "sub": "U-lab", "iss": "https://access.line.me"}
        self.post.return_value.json.return_value = self.claims

    def test_liff_uses_login_channel_not_oa(self):
        self.assertEqual(line.verify_id_token("token"), self.claims)
        self.assertEqual(self.post.call_args.kwargs["data"]["client_id"], "1111111111")

    def test_no_liff_never_falls_back_to_messaging_channel(self):
        for value in ("", "invalid", "abc-app", "123-"):
            self.config["pearnly_line_liff_id"] = value
            self.assertIsNone(line.verify_id_token("token"))
        self.post.assert_not_called()

    def test_cross_channel_and_malformed_claims_rejected(self):
        for claims in ({**self.claims, "aud": "2222222222"}, {}, [],
                       {**self.claims, "iss": "other"}, {**self.claims, "sub": ""}):
            self.post.return_value.json.return_value = claims
            self.assertIsNone(line.verify_id_token("token"))

    def test_loading_accepts_202_and_network_error_does_not_stop_work(self):
        self.post.return_value.status_code = 202
        self.assertTrue(line.start_loading("U-lab", 63))
        self.assertEqual(self.post.call_args.kwargs["json"]["loadingSeconds"], 60)
        self.post.side_effect = requests.Timeout("local fixture")
        self.assertFalse(line.start_loading("U-lab"))

    def test_background_loading_sends_the_same_request_and_reports_before_replies(self):
        self.post.return_value.status_code = 202
        wait = line.start_loading_background("U-lab", 63)
        self.assertTrue(wait())
        self.assertEqual(self.post.call_args.kwargs["json"], {"chatId": "U-lab", "loadingSeconds": 60})
        self.assertEqual(self.post.call_args.kwargs["timeout"], (1, 1))
        self.assertEqual(self.post.call_args.kwargs["headers"]["Authorization"], "Bearer lab-token")
        self.post.side_effect = requests.Timeout("local fixture")
        self.assertFalse(line.start_loading_background("U-lab")())
        self.post.reset_mock()
        self.config["pearnly_line_channel_access_token"] = ""
        self.assertFalse(line.start_loading_background("U-lab")())
        self.post.assert_not_called()

    def test_media_download_uses_the_callers_session(self):
        from unittest.mock import MagicMock
        session = MagicMock()
        response = session.get.return_value.__enter__.return_value
        response.status_code, response.headers = 200, {"Content-Length": "3"}
        response.iter_content.return_value = [b"img"]
        with patch.object(line.requests, "get") as fresh:
            self.assertEqual(line.download_message_content("m1", max_bytes=10, session=session), b"img")
        fresh.assert_not_called()
        self.assertIn("/v2/bot/message/m1/content", session.get.call_args.args[0])


class DmsMediaConnection(unittest.TestCase):
    def setUp(self):
        from pearnly_dms.line import out
        self.out = out
        self.enterContext(patch.object(out, "_media_session", None))
        self.download = self.enterContext(patch.object(line, "download_message_content", return_value=b"img"))

    def test_downloads_reuse_one_connection_pool_without_cookies(self):
        self.assertEqual(self.out.download_image("m1", 10), b"img")
        self.assertEqual(self.out.download_image("m2", 10), b"img")
        first, second = (call.kwargs["session"] for call in self.download.call_args_list)
        self.assertIs(first, second)
        self.assertEqual(self.download.call_args.kwargs["max_bytes"], 10)
        from http.cookiejar import Cookie
        cookie = Cookie(0, "probe", "1", None, False, "api-data.line.me", True, False, "/", True,
                        True, None, False, None, None, {})
        from requests.cookies import MockRequest
        request = MockRequest(requests.Request("GET", "https://api-data.line.me/v2/bot/message/m1/content").prepare())
        self.assertFalse(first.cookies.get_policy().set_ok(cookie, request))

    def test_dropped_reused_connection_downloads_once_more_on_a_fresh_one(self):
        self.download.side_effect = [requests.ConnectionError("closed by peer"), b"img"]
        self.assertEqual(self.out.download_image("m1", 10), b"img")
        first, second = (call.kwargs["session"] for call in self.download.call_args_list)
        self.assertIsNot(first, second)

    def test_id_card_loading_runs_alongside_and_lands_before_the_reply(self):
        from pearnly_dms.intake import flow
        events = []
        self.enterContext(patch.object(flow, "_image_draft", return_value={"state": "collecting"}))
        self.enterContext(patch.object(flow.out, "start_loading_background",
                                       return_value=lambda: events.append("loading landed")))
        recognize = self.enterContext(patch.object(flow, "_recognize_image",
                                                   side_effect=lambda *a: events.append("recognized") or {"ok": 1}))
        self.assertEqual(flow.process_image({"user_id": "u"}, "U-lab", "m1", "tok"), {"ok": 1})
        self.assertEqual(events, ["recognized", "loading landed"])
        recognize.assert_called_once_with({"user_id": "u"}, "U-lab", "m1", "tok")
        events.clear()
        recognize.side_effect = RuntimeError("local fixture")
        with self.assertRaises(RuntimeError):
            flow.process_image({"user_id": "u"}, "U-lab", "m1", "tok")
        self.assertEqual(events, ["loading landed"])

    def test_unreachable_server_is_not_retried(self):
        self.download.side_effect = requests.ConnectTimeout("local fixture")
        with self.assertRaises(requests.ConnectTimeout):
            self.out.download_image("m1", 10)
        self.assertEqual(self.download.call_count, 1)


class LineTokenNotLogged(unittest.TestCase):
    """Frappe 记错误时打印每层变量；LINE 令牌不能出现在里面（2026-10-03 菜单同步的错误日志存了令牌）。"""

    TOKEN = "lab-secret-channel-token"

    def setUp(self):
        self.enterContext(patch.object(frappe, "conf", frappe._dict(
            pearnly_line_channel_access_token=self.TOKEN, pearnly_dms_rich_menus={"basic": "b", "query": "q"})))
        self.request = self.enterContext(patch.object(line.requests, "request"))

    def logged(self, call):
        try:
            call()
        except Exception:
            return frappe.get_traceback(with_context=True)
        self.fail("expected an error")

    def test_network_error_keeps_its_type_without_the_token(self):
        self.request.side_effect = requests.ConnectionError("Failed to resolve 'api.line.me'")
        with self.assertRaises(requests.ConnectionError) as raised:
            line.request("GET", "https://api.line.me/v2/bot/info", timeout=5)
        self.assertIn("Failed to resolve", str(raised.exception))
        self.assertEqual(self.request.call_args.kwargs["headers"]["Authorization"], "Bearer " + self.TOKEN)
        self.assertNotIn(self.TOKEN, self.logged(lambda: line.request("GET", "https://api.line.me/v2/bot/info")))

    def test_rich_menu_errors_do_not_log_the_token(self):
        from pearnly_dms.menu import rich_menu
        self.request.return_value = Mock(status_code=500, text="LINE fixture", content=b"")
        self.assertNotIn(self.TOKEN, self.logged(lambda: rich_menu._request("GET", "richmenu/list")))
        self.request.side_effect = requests.ReadTimeout("read timed out")
        self.assertNotIn(self.TOKEN, self.logged(lambda: rich_menu._request("GET", "richmenu/list")))
        self.assertNotIn(self.TOKEN, self.logged(lambda: rich_menu._has_image("menu-1")))
        self.request.side_effect = None
        self.request.return_value = Mock(status_code=200, content=b"png")
        rich_menu._request("POST", "richmenu/m/content", image=b"png")
        self.assertEqual(self.request.call_args.kwargs["headers"]["Content-Type"], "image/png")

    def test_user_id_rejected_by_line_is_paused_for_a_day_without_an_error(self):
        from pearnly_dms.menu import rich_menu
        values = {}
        cache = Mock(get_value=values.get, make_key=lambda key: key,
                     set_value=lambda key, value, expires_in_sec=None: values.__setitem__(key, (value, expires_in_sec)))
        cache.lock.return_value = nullcontext()
        cache.get_value = lambda key: (values.get(key) or (None,))[0]
        self.enterContext(patch.object(frappe, "cache", lambda: cache))
        self.enterContext(patch.object(rich_menu.line_binding, "binding_of", return_value=Mock(user="u@x")))
        self.enterContext(patch.object(rich_menu, "can_query", return_value=False))
        self.request.return_value = Mock(status_code=400, content=b"x",
                                         text='{"message":"The value for the \'userId\' parameter is invalid"}')
        rich_menu.sync("Udeploy-fake", language="th")
        self.assertEqual(values[rich_menu._APPLIED + "Udeploy-fake"], (rich_menu._REJECTED, 86400))
        calls = self.request.call_count
        rich_menu.sync("Udeploy-fake", language="th")
        self.assertEqual(self.request.call_count, calls, "a rejected id is not retried within the day")
        # 其他 400（例如菜单 ID 不对，是我们自己的问题）照样报错。
        self.request.return_value = Mock(status_code=400, content=b"x", text='{"message":"richmenu not found"}')
        with self.assertRaises(RuntimeError):
            rich_menu.sync("U-real", language="th")


class _FakeState:
    """不淘汰 Redis 的替身：只认扫描用到的几个调用。"""

    def __init__(self):
        self.values = {}

    def make_key(self, key):
        return "site|" + key

    def set_value(self, key, value, expires_in_sec=None):
        self.values[key] = value

    def get_value(self, key):
        return self.values.get(key)

    def set(self, key, value, ex=None, nx=False):
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True


class DmsIdScan(unittest.TestCase):
    """LINE 里「扫身份证」：直传照片 → 同一条识别；旧的发照片流程不变。"""

    JPEG = b"\xff\xd8\xff\xe0" + b"x" * 64

    def setUp(self):
        import os
        import tempfile
        from pearnly_core import line_session
        from pearnly_dms.intake import flow, scan
        from pearnly_dms.line import identity

        self.scan, self.flow = scan, flow
        root = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, root, True)
        self.enterContext(patch.object(frappe, "get_site_path", lambda *parts: os.path.join(root, *parts)))
        self.enterContext(patch.object(frappe.local, "site", "testdms.local", create=True))
        self.enterContext(patch.object(frappe.local, "flags", frappe._dict(), create=True))
        self.state = _FakeState()
        self.enterContext(patch.object(scan, "_state", return_value=self.state))
        self.session = {"state": "collecting", "payload": {"mode": "customer"}}
        self.enterContext(patch.object(line_session, "get_session", side_effect=lambda uid: self.session))
        self.enterContext(patch.object(identity, "current", return_value={"user": "sales@x", "binding_revision": "r1"}))
        self.merge = self.enterContext(patch.object(flow, "merge_session", side_effect=self._merge))
        self.begin = self.enterContext(patch.object(flow, "handle_image"))
        self.enterContext(patch.object(line, "liff_id", return_value="1234-abcd"))
        self.reply = self.enterContext(patch.object(line, "reply"))
        for name, value in (("BTN_SCAN_CARD", "Scan ID card"), ("TXT_SCAN_SENT", "ID card scanned"),
                            ("TXT_EXPIRED", "Expired")):
            self.enterContext(patch.object(scan.cards, name, value, create=True))

    def _merge(self, binding, line_user_id, fields, keep=(), sess=None):
        self.session = {"state": "collecting", "payload": {**self.session.get("payload", {}), **fields}}
        return self.session["payload"]

    def test_prompt_offers_the_scan_button_only_when_the_site_has_liff(self):
        message = self.scan.prompt("Please send the ID card photo")
        action = message["quickReply"]["items"][0]["action"]
        self.assertEqual((message["text"], action["type"]), ("Please send the ID card photo", "uri"))
        self.assertEqual(action["uri"], "https://liff.line.me/1234-abcd?editor=scan")
        self.assertLessEqual(len(action["label"]), 20)
        with patch.object(line, "liff_id", return_value=""):
            self.assertEqual(self.scan.prompt("x"), {"type": "text", "text": "x"})

    def test_upload_registers_the_photo_as_the_image_to_read(self):
        result = self.scan.store_upload("U-sales", self.JPEG)
        pending = self.session["payload"]["pending_image"]
        self.assertTrue(pending.startswith("scan:") and pending[5:].startswith(result["code"]))
        self.assertEqual(result["marker"], f"ID card scanned #{result['code']}", "plain text, no icon")
        self.assertEqual(self.session["payload"]["mode"], "customer", "phone / mode stay as with a chat photo")
        self.assertEqual(self.scan.read(pending, "U-sales"), self.JPEG)
        self.assertEqual(self.scan.read(pending, "U-sales"), self.JPEG, "kept for the booking attachment")
        self.begin.assert_not_called()

    def test_upload_rejects_non_images_oversize_and_other_steps(self):
        with self.assertRaises(self.scan.ScanError) as raised:
            self.scan.store_upload("U-sales", b"%PDF-1.7")
        self.assertEqual(raised.exception.code, "dms_scan.not_image")
        with patch("pearnly_dms.ocr.ocr_service.MAX_ID_CARD_MB", 0):
            with self.assertRaises(self.scan.ScanError):
                self.scan.store_upload("U-sales", self.JPEG)
        self.session = {"state": "reviewing", "payload": {}}
        with self.assertRaises(self.scan.ScanError) as raised:
            self.scan.store_upload("U-sales", self.JPEG)
        self.assertEqual((raised.exception.code, raised.exception.status), ("dms_booking.expired", 409))

    def test_marker_in_chat_starts_recognition_once_with_its_free_reply_token(self):
        from pearnly_dms.line import text_router

        result = self.scan.store_upload("U-sales", self.JPEG)
        pending = self.session["payload"]["pending_image"]
        binding = {"user_id": "sales@x", "tenant_id": "testdms.local"}
        with patch.object(text_router, "capture_phone", side_effect=AssertionError("marker is not a phone")):
            text_router.route(binding, "U-sales", "tok-1", result["marker"])
            text_router.route(binding, "U-sales", "tok-2", result["marker"])
        self.begin.assert_called_once()
        self.assertEqual(self.begin.call_args.args[1:], ("U-sales", pending, "tok-1"))

    def test_stale_marker_says_expired_and_starts_nothing(self):
        self.scan.store_upload("U-sales", self.JPEG)
        from pearnly_dms.line import page_reply

        self.assertTrue(page_reply.route("U-sales", "tok", "ID card scanned #000000"))
        self.assertFalse(page_reply.route("U-sales", "tok", "0812345678"))
        self.begin.assert_not_called()
        self.assertEqual(self.reply.call_args.args[1][0]["text"], "Expired")

    def test_page_fallback_starts_without_a_token_and_only_once(self):
        result = self.scan.store_upload("U-sales", self.JPEG)
        self.assertTrue(self.scan.start("U-sales", result["code"]))
        from pearnly_dms.line import page_reply

        self.assertTrue(page_reply.route("U-sales", "tok", result["marker"]))
        self.begin.assert_called_once()
        self.assertEqual(self.begin.call_args.args[3], "")
        with self.assertRaises(self.scan.ScanError):
            self.scan.start("U-sales", "ffffff")

    def test_booking_attaches_the_scanned_card_from_the_site_not_line(self):
        from pearnly_dms.booking import attachments
        from pearnly_dms.line import out

        self.scan.store_upload("U-sales", self.JPEG)
        pending = self.session["payload"]["pending_image"]
        with patch.object(out, "download_content", side_effect=AssertionError("not a LINE message")):
            done, failed = attachments.download({"files": {"id_card_mid": pending}})
        self.assertEqual((failed, done[0]["display_name"], done[0]["content"]),
                         ([], "สำเนาบัตรประชาชน", self.JPEG))
        with patch.object(out, "download_content", return_value=b""):
            _, failed = attachments.download({"files": {"id_card_mid": "scan:" + "0" * 16}})
        self.assertEqual(failed[0]["error"], "line content download failed", "a missing scan is reported, not hidden")

    def test_only_the_uploader_can_read_the_photo(self):
        self.scan.store_upload("U-sales", self.JPEG)
        pending = self.session["payload"]["pending_image"]
        self.assertEqual(self.scan.read(pending, "U-other"), b"")
        self.assertEqual(self.scan.read("scan:../../etc", "U-sales"), b"")

    def test_recognition_reads_the_scan_instead_of_downloading_from_line(self):
        from pearnly_dms.line import out
        from pearnly_dms.ocr import ocr_service

        self.scan.store_upload("U-sales", self.JPEG)
        pending = self.session["payload"]["pending_image"]
        seen = {}

        def recognize(user, content, filename, *args, **kwargs):
            seen.update(content=content, reference=kwargs["billing_reference"])
            return {"id": "EP"}, {"needs_review": True, "missing_fields": ["people_id"]}, 0

        sent = []
        with patch.object(out, "download_image", side_effect=AssertionError("no LINE download")), \
             patch.object(self.flow, "_image_draft", return_value=self.session), \
             patch.object(ocr_service, "recognize_id_card", side_effect=recognize), \
             patch.object(self.flow, "_send", side_effect=lambda uid, msg, token="": sent.append(msg)), \
             patch.object(frappe, "db", Mock(), create=True):
            self.flow._recognize_image({"user_id": "sales@x"}, "U-sales", pending, "tok")
        self.assertEqual(seen, {"content": self.JPEG, "reference": f"idcard:line:{pending}"})
        self.assertEqual(sent[0]["quickReply"]["items"][0]["action"]["type"], "uri",
                         "the retake prompt offers the scan button again")

    def test_upload_api_needs_a_scan_ticket_and_takes_a_data_url(self):
        import base64
        from pearnly_dms.editor import browser_api

        stored = self.enterContext(patch.object(self.scan, "store_upload", return_value={"code": "abc123"}))
        self.enterContext(patch("pearnly_dms.i18n.line_language", return_value=nullcontext()))
        image = "data:image/jpeg;base64," + base64.b64encode(self.JPEG).decode()
        with patch.object(browser_api.tickets, "resolve", return_value={"line_user_id": "U-sales", "mode": "scan"}):
            self.assertEqual(browser_api.browser_scan("t", image), {"code": "abc123"})
        self.assertEqual(stored.call_args.args, ("U-sales", self.JPEG))
        class Refused(Exception):
            pass

        def refuse(code, status=400):
            raise Refused(code, status)

        with patch.object(browser_api.tickets, "resolve", return_value={"line_user_id": "U-sales", "mode": "customer"}), \
             patch.object(browser_api, "_fail", side_effect=refuse), self.assertRaises(Refused) as raised:
            browser_api.browser_scan("t", image)
        self.assertEqual(raised.exception.args, ("dms_booking.line_auth_required", 403))


class _FakeRedis(_FakeState):
    """加上底层 get / delete（慢任务结果用的是底层读写）。"""

    def get(self, key):
        return self.values.get(key)

    def delete(self, key):
        self.values.pop(key, None)

    def delete_value(self, key):
        self.values.pop(key, None)


class DmsPageReply(unittest.TestCase):
    """LINE 页面（扫描、编辑）做完后替业务员发一句纯文字，webhook 用它的令牌免费回覆。"""

    def setUp(self):
        from pearnly_dms.line import page_reply

        self.page_reply = page_reply
        self.state = _FakeRedis()
        self.enterContext(patch.object(page_reply, "_state", return_value=self.state))
        self.send = self.enterContext(patch.object(line, "reply_or_push", return_value=True))
        self.reply = self.enterContext(patch.object(line, "reply"))

    def test_the_sentence_is_plain_text_and_recognised_in_every_language(self):
        from pearnly_dms import i18n

        for kind, key in self.page_reply.KINDS.items():
            for lang, label in i18n.all_languages(f"dms.card.{key}").items():
                # 不加图标：没有表情符号（U+2600–27BF、U+1F000 起）。
                self.assertFalse(any(0x2600 <= ord(ch) <= 0x27BF or ord(ch) >= 0x1F000 for ch in label), (lang, label))
                self.assertEqual(self.page_reply.parse(f"{label} #a1b2c3"), (kind, "a1b2c3"), (lang, label))
        for text in ("0812345678", "#a1b2c3", "hello #a1b2c3", "แก้ไขข้อมูลแล้ว #12345"):
            self.assertIsNone(self.page_reply.parse(text), text)

    def test_saved_card_goes_back_once_with_the_free_reply_token(self):
        with patch.object(frappe.local, "dms_line_lang", "zh", create=True):
            held = self.page_reply.hold("U-sales", [{"type": "flex", "altText": "card"}])
        self.assertEqual(held["marker"], f"资料已修改 #{held['code']}")
        self.assertTrue(self.page_reply.route("U-sales", "tok-1", held["marker"]))
        self.send.assert_called_once_with("U-sales", [{"type": "flex", "altText": "card"}], "tok-1")
        self.assertTrue(self.page_reply.route("U-sales", "tok-2", held["marker"]))
        self.assertEqual(self.send.call_count, 1, "the card is sent once")
        self.reply.assert_called_once()

    def test_page_fallback_pushes_and_only_the_owner_can_take_the_card(self):
        held = self.page_reply.hold("U-sales", [{"type": "text", "text": "card"}])
        self.assertIsNone(self.page_reply.deliver("U-other", held["code"]))
        self.assertTrue(self.page_reply.deliver("U-sales", held["code"]))
        self.assertEqual(self.send.call_args.args[2], "", "no token: push")
        self.assertIsNone(self.page_reply.deliver("U-sales", held["code"]))

    def test_editor_save_keeps_the_card_for_the_page_sentence(self):
        from pearnly_dms.editor import browser_api

        def save(line_user_id, nonce, submitted, deliver=None):
            self.assertTrue(deliver(line_user_id, {"type": "flex"}))
            return "n2"

        editor = Mock(save=save)
        self.enterContext(patch.object(browser_api.tickets, "resolve",
                                       return_value={"line_user_id": "U-sales", "mode": "customer"}))
        self.enterContext(patch.object(browser_api, "_editor", return_value=editor))
        self.enterContext(patch("pearnly_dms.i18n.line_language", return_value=nullcontext()))
        result = browser_api.browser_save("t", "n1", "{}")
        self.assertEqual(result["nonce"], "n2")
        self.assertTrue(result["marker"].endswith("#" + result["code"]))
        self.send.assert_not_called()


class DmsSlowReply(unittest.TestCase):
    """慢任务：令牌快过期先免费回「还在处理」，结果存起来，点「查看结果」再免费回。"""

    def setUp(self):
        from pearnly_dms.line import out

        self.out = out
        self.state = _FakeRedis()
        self.enterContext(patch.object(out, "_state", return_value=self.state))
        self.enterContext(patch.object(out, "SLOW_AFTER", 0.05))
        self.sent = []
        self.enterContext(patch.object(line, "reply_sender",
                                       return_value=lambda messages: self.sent.append(messages) or True))
        self.reply = self.enterContext(patch.object(line, "reply"))
        from pearnly_dms.line import cards

        for name, value in (("TXT_STILL_WORKING", "Still working"), ("BTN_SHOW_RESULT", "View result"),
                            ("TXT_NOT_READY", "Not yet"), ("TXT_EXPIRED", "Expired"),
                            ("TXT_MORE_RESULTS", "More")):
            self.enterContext(patch.object(cards, name, value, create=True))

    def batch(self, messages):
        return patch.object(frappe.local, "pearnly_line_batch",
                            {"to": "U-sales", "token": "tok", "origin": "tok", "used": False,
                             "messages": list(messages)}, create=True)

    def test_slow_job_replies_still_working_then_hands_the_result_on_tap(self):
        import time as clock

        self.out.note_reply_token("tok")
        with self.batch([{"type": "text", "text": "done"}]):
            slow = self.out._SlowReply("U-sales", "tok")
            clock.sleep(0.2)
            self.assertEqual(self.sent[0][0]["text"], "Still working")
            self.assertEqual(self.sent[0][0]["quickReply"]["items"][0]["action"]["data"], "action=show_result")
            self.out.show_result("U-sales", "tok-wait")
            self.assertEqual(self.reply.call_args.args[1][0]["text"], "Not yet")
            slow.finish()
            self.assertEqual(frappe.local.pearnly_line_batch["messages"], [], "nothing is pushed")
            self.assertTrue(frappe.local.pearnly_line_batch["used"])
        self.out.show_result("U-sales", "tok-tap")
        self.assertEqual(self.reply.call_args.args, ("tok-tap", [{"type": "text", "text": "done"}]))
        self.out.show_result("U-sales", "tok-again")
        self.assertEqual(self.reply.call_args.args[1][0]["text"], "Expired")

    def test_fast_job_and_stale_token_change_nothing(self):
        import time as clock

        self.out.note_reply_token("tok")
        with patch.object(self.out, "SLOW_AFTER", 5), self.batch([{"type": "text", "text": "done"}]):
            slow = self.out._SlowReply("U-sales", "tok")
            slow.finish()
            self.assertEqual(frappe.local.pearnly_line_batch["messages"], [{"type": "text", "text": "done"}])
        clock.sleep(0.1)
        self.assertEqual(self.sent, [])
        self.state.values[self.out._token_key("old")] = clock.time() - 58
        self.assertIsNone(self.out._SlowReply("U-sales", "old").timer, "a token about to expire is not used")
        self.assertIsNone(self.out._SlowReply("U-sales", "").timer)


class DmsReviewLanguage(unittest.TestCase):
    """识别打回的提示按收件人手机语言说（2026-10-04：中文手机上括号里是泰文字段名、校验位那句整句泰文）。"""

    def say(self, language, missing):
        from pearnly_dms import i18n
        from pearnly_dms.ocr import ocr_review

        with patch.object(frappe.local, "dms_line_lang", language, create=True):
            return ocr_review.review_text(missing), i18n

    def test_missing_fields_follow_the_recipient_language(self):
        text, i18n = self.say("zh", ["people_id", "first_name"])
        blurry = i18n.t("dms.card.TXT_BLURRY", lang="zh")
        self.assertEqual(text, f"{blurry}（身份证号、名）")
        text, _ = self.say("en", ["last_name", "birthday"])
        self.assertTrue(text.endswith("(Last name, Date of birth)"), text)
        text, _ = self.say("th", ["people_id"])
        self.assertTrue(text.endswith("(เลขบัตรประชาชน)"), text)

    def test_checksum_sentence_is_translated_and_thai_keeps_its_wording(self):
        text, _ = self.say("zh", ["people_id_checksum"])
        self.assertEqual(text, "身份证号校验位不符，请核对卡面号码后重新拍照，确保号码清晰可见。")
        text, _ = self.say("th", ["people_id_checksum"])
        self.assertTrue(text.startswith("อ่านเลขบัตรประชาชนได้ครบ 13 หลัก แต่หลักตรวจสอบไม่ผ่าน"), text)
        text, _ = self.say("ja", [])
        self.assertNotIn("dms.card", text, "every language has the blurry sentence")


class OcrBackoff(unittest.TestCase):
    def setUp(self):
        from pearnly_core.execution import provider
        self.provider = provider
        self.post = self.enterContext(patch.object(provider, "_http_post"))  # 识别调用的 HTTPS 出口（共用 TLS 上下文）
        self.enterContext(patch.object(provider.budget, "permit", return_value=nullcontext()))
        self.feedback = self.enterContext(patch.object(provider.budget, "feedback"))
        self.checkpoint = self.enterContext(patch.object(provider.work, "checkpoint", return_value=None))
        self.enterContext(patch.object(provider.random, "uniform", return_value=0))

    def response(self, status, retry="0"):
        result = Mock(status_code=status, headers={"Retry-After": retry})
        result.json.return_value = {"usageMetadata": {"promptTokenCount": 10}}
        result.iter_content.return_value = [b'{"usageMetadata":{"promptTokenCount":10}}']
        return result

    def call(self):
        return self.provider.post("https://example.test/ocr", {}, {"test": True}, 120, "test-model", "bucket")

    def test_transient_deferred_without_immediate_paid_replay(self):
        from pearnly_core.execution.errors import Deferred
        for status in (429, 503):
            self.post.reset_mock()
            self.post.return_value = self.response(status, "7")
            with self.assertRaises(Deferred) as result:
                self.call()
            self.assertEqual(result.exception.delay, 7)
            self.assertEqual(self.post.call_count, 1)
            self.assertEqual(self.post.call_args.kwargs["json"], {"test": True})
            self.checkpoint.assert_called_with(self.provider.work.checkpoint_key({"test": True}, "test-model"),
                                               "rejected", {"http": status})

    def test_permanent_failure_and_ambiguous_timeout_never_replayed(self):
        from pearnly_core.execution.errors import Uncertain
        for status in (400, 401, 403):
            self.post.reset_mock()
            self.post.return_value = self.response(status)
            self.assertEqual(self.call().status_code, status)
            self.assertEqual(self.post.call_count, 1)
        self.post.side_effect = requests.Timeout()
        with self.assertRaises(Uncertain):
            self.call()

    def test_retry_after_bound_and_completed_checkpoint(self):
        from pearnly_core.execution.errors import Deferred
        self.post.return_value = self.response(429, "900")
        with self.assertRaises(Deferred) as result:
            self.call()
        self.assertEqual(result.exception.delay, 300)
        self.post.reset_mock()
        self.checkpoint.return_value = {"state": "done", "body": {"usageMetadata": {"promptTokenCount": 10}}}
        response = self.call()
        self.assertEqual(response.json()["usageMetadata"]["promptTokenCount"], 10)
        response.close()
        self.post.assert_not_called()


class SubmissionEvidence(unittest.TestCase):
    def test_submitted_unknown_survives_service_error_conversion(self):
        from unittest.mock import MagicMock
        adapter = MagicMock()
        adapter.concurrent_login_detected = True
        evidence = {"submitted": True, "booking_no": "LAB-UNKNOWN", "mismatches": ["paint"]}
        failure = DmsClientError("unverified stored result", "ERR_DMS_BOOKING_UNKNOWN")
        failure.response_body = evidence
        with patch("pearnly_dms.adapter.operations.build_adapter", return_value=(adapter, None)):
            result = intake_service.run_logged_in("EP-lab", Mock(side_effect=failure))
        self.assertEqual(result["error_code"], "ERR_DMS_BOOKING_UNKNOWN")
        for key, value in evidence.items():
            self.assertEqual(result["response_body"][key], value)


class VerifiedCustomerWriteAndKick(unittest.TestCase):
    """A customer write already read back field by field is not undone by a later kick."""

    def run_with_kick(self, out):
        from unittest.mock import MagicMock
        adapter = MagicMock()
        adapter.concurrent_login_detected = True
        adapter.has_distinct_admin_creds = False
        adapter.last_dialog = "duplicate login"
        with patch("pearnly_dms.adapter.operations.build_adapter", return_value=(adapter, None)):
            return intake_service.run_logged_in("EP-lab", Mock(return_value=out))

    def test_verified_customer_write_and_booking_count_other_results_do_not(self):
        # Old bug: the verified write became ERR_DMS_CONCURRENT_LOGIN, then "unconfirmed",
        # and the customer stayed blocked although DMS had the change.
        for proven in ({"ok": True, "success": True, "customer_id": "own", "cuscode": "C1"},
                       {"ok": True, "booking_id": "external-lab-1", "booking_no": "LAB-1"}):
            self.assertIs(self.run_with_kick(proven), proven)
        for unproven in ({"ok": True}, {"ok": True, "success": True}, {"ok": True, "customer_id": "own"},
                         {"ok": True, "rows": []}):
            self.assertEqual(self.run_with_kick(unproven)["error_code"], "ERR_DMS_CONCURRENT_LOGIN")


class BookingRetryRule(unittest.TestCase):
    def test_unconfirmed_customer_write_before_the_order_gets_the_retry_card(self):
        from pearnly_dms.booking.conversation import booking_flow
        unconfirmed = {"error_code": "ERR_DMS_CUSTOMER_UNCONFIRMED",
                       "response_body": {"submitted": True, "retry_safe": False}}
        self.assertTrue(booking_flow._retryable_result(unconfirmed))
        self.assertFalse(booking_flow._retryable_result({**unconfirmed, "booking_id": "external-lab-1"}))
        for code in ("ERR_DMS_BOOKING_OUTCOME_UNKNOWN", "ERR_DMS_CUSTOMER_SAVE", ""):
            self.assertFalse(booking_flow._retryable_result(
                {"error_code": code, "response_body": {"submitted": True}}))


class SalesQuery(unittest.TestCase):
    def test_named_field_filters_before_pagination(self):
        from pearnly_dms.query import sales_readback as query
        rows = [{"id": str(i), "booking_date": "20/09/2569" if i in (3, 9, 11) else "19/09/2569",
                 "delivery_date": "20/09/2569"} for i in range(200)]
        client = Mock()
        with patch.object(query, "_sales_page", side_effect=[rows, []]):
            found, more = query._field_page(client, "booking_date", "2", "1", "20/09/2569", 2, 1)
        self.assertEqual([row["id"] for row in found], ["3", "9"])
        self.assertTrue(more)
        with patch.object(query, "_sales_page", side_effect=[rows, []]):
            found, more = query._field_page(client, "booking_date", "2", "1", "20/09/2569", 2, 2)
        self.assertEqual([row["id"] for row in found], ["11"])
        self.assertFalse(more)

    def test_repeated_page_and_invalid_response_are_not_empty_success(self):
        from pearnly_dms.query import sales_readback as query
        rows = [{"id": str(i), "customer": "other"} for i in range(200)]
        with patch.object(query, "_sales_page", return_value=rows), self.assertRaises(DmsClientError):
            query._field_page(Mock(), "customer", "5", "1", "missing", 10, 1)
        for body in ("", "<form>Login</form>", '<div data-val="1"><p>incomplete</p></div>'):
            client = Mock()
            client._post_text.return_value = body
            with self.assertRaises(DmsClientError):
                query._sales_page(client, "1", "1", "", 10, 1)

    def test_ranking_respects_native_account_scope_and_array_contract(self):
        from pearnly_dms.query import snapshot_sync as sync, sales_readback as query
        client, adapter = Mock(), Mock()
        adapter.base_url = "https://example.test/dms/"
        home = '<input id="idusers" value="own-id">'
        adapter.transport.return_value.get.return_value.text = home
        adapter.transport.return_value.get.return_value.status_code = 200
        scope = sync.dashboard_scope(adapter)
        self.assertFalse(scope["top_sales"])
        with self.assertRaises(DmsClientError) as failure:
            sync.collect_top(client, scope, query.top_parameters())
        self.assertEqual(failure.exception.error_code, "ERR_DMS_QUERY_SCOPE")
        client._post_text.assert_not_called()
        adapter.transport.return_value.get.return_value.text = home + ''.join(
            f'<select id="{field}"><option value="0">All</option><option value="7">Own scope</option></select>'
            for field in ("carmaxsellbranch", "carmaxsellteam"))
        scope = sync.dashboard_scope(adapter)
        self.assertTrue(scope["top_sales"])
        client._post_text.side_effect = ["2", '[["A", "B"], ["1"], [1, 2]]']
        with self.assertRaises(DmsClientError):
            sync.collect_top(client, scope, query.top_parameters())

    def test_rebound_identity_receives_no_result_after_slow_read(self):
        from pearnly_dms.query import query_flow as flow
        for method, read, params in (
            (flow._run_records, "fetch_sales_records", dict(field="customer", query="fixture", status="active", limit=10, page=1)),
            (flow._run_top, "fetch_top_sales", dict(group="model", metric="quantity", date_from="", date_to="", limit=10, page=1)),
        ):
            with patch.object(flow, "_can_query", return_value=True), \
                    patch.object(frappe, "db", Mock()) as db, \
                    patch.object(flow, "current_binding", return_value=False) as live, \
                    patch.object(flow.sales_readback, read, return_value={"ok": True, "rows": []}), \
                    patch.object(flow, "_push") as push, patch.object(flow, "_send") as send:
                method({"user_id": "old-employee", "binding_revision": "old"}, "U-lab", params)
                db.commit.assert_called_once()
                live.assert_called_once()
                push.assert_not_called()
                send.assert_not_called()

    def test_fresh_session_initializes_native_list_before_query(self):
        from pearnly_dms.query import sales_readback as query, snapshot_sync as sync
        client = Mock()
        initialized = False

        def post(path, values):
            nonlocal initialized
            if path == "drfcbc/view.php":
                self.assertEqual(values, {"idmenu": "25", "menulv": "2"})
                initialized = True
                return "native view"
            self.assertTrue(initialized, "A direct query silently hides existing DMS records")
            self.assertEqual(path, query.LIST_PATH)
            if "sdtamt" not in values:
                return "dt::1"
            cells = ["", "active", "BK-LAB", "20/09/2569", "21/10/2569",
                     "Salesperson", "Customer", "Model", "Red", "cash", "", "", ""]
            return '<div data-val="lab">' + ''.join('<p>' + c + '</p>' for c in cells) + '</div>'

        client._post_text.side_effect = post
        result = sync.collect_sales(client, query.query_parameters())
        self.assertEqual(result["rows"][0]["booking_no"], "BK-LAB")


class BookingService(unittest.TestCase):
    def setUp(self):
        self.phase = "sales"
        self.client = Mock()
        self.client.create_booking_via_form.side_effect = self.submit
        self.db = Mock()
        self.db.get_value.side_effect = self.db_value
        self.enterContext(patch.object(frappe, "db", self.db))
        self.enterContext(patch("pearnly_dms.adapter.operations.run_logged_in", side_effect=self.run_session))
        self.enterContext(patch("pearnly_dms.adapter.admin_read.authoritative_read_session", self.read_session))
        self.enterContext(patch.object(service, "booking_lock", self.write_lock))
        self.payload = {"qa": {
            "customer": {"id": "customer-lab", "name": "Edited Name"},
            "draft": {"people_id": "fixture-id", "phone": "0812345678"},
            "advisor": {"id": "a", "name": "Advisor"},
            "answers": {**{k: {"id": k} for k in ("car", "paint", "place", "term", "regis")},
                        "delivery_date_be": "22/10/2569", "regis_name": "Edited Registration"},
            "payments": [{"channel": "cash", "amount": "10.25", "extra": {}}],
        }}
        self.masters = {key: [[rid, rid, "Live " + rid]] for key, rid in (
            ("cars", "car"), ("advisors", "a"), ("place_books", "place"),
            ("term_sales", "term"), ("regis_behalfs", "regis"))}
        # ADR-014: pre-submit master check reads the local complete snapshot, never an admin session.
        self.preflight = self.enterContext(patch("pearnly_dms.booking.booking_preflight.local_preflight"))
        self.preflight.side_effect = self.read_preflight
        ref = DMSMasterRef("org", "ORG", "Organization")
        self.enterContext(patch("pearnly_dms.booking.booking_org.resolve_booking_org",
                                return_value=BookingOrganization(ref, ref, ())))
        self.enterContext(patch("pearnly_dms.booking.booking_customer.card_from_customer", return_value=object()))
        self.enterContext(patch("pearnly_dms.masters.company_banks.validate_company_bank_payments",
                                side_effect=lambda adapter, payments, **kw: payments))
        self.cache = self.enterContext(patch("pearnly_dms.adapter.snapshot_writeback.sales", return_value=True))
        self.attempt = Mock()

    def db_value(self, doctype, name, field):
        self.assertEqual(name, "EP-lab")  # dict-as-filter caused the production SQL error
        return "{}" if field == "booking_defaults" else "https://example.test/dms/index.php"

    def run_session(self, endpoint, fn, **kwargs):
        self.assertEqual(endpoint, "EP-lab")
        # 操作员自己发起的那一笔：动作之外还要顺手把使用记录里的增量补回缓存；
        # 登录页都没打开时同一任务自动再试一次（`line_write`，2026-10-13）。
        self.assertEqual(kwargs, {"catch_up": True, "line_write": True})
        return fn(self.client, object())

    @contextmanager
    def read_session(self, client):
        self.phase = "authority"
        try:
            yield client
        finally:
            self.phase = "sales"

    @contextmanager
    def write_lock(self, endpoint):
        self.assertEqual(self.phase, "sales")
        yield True

    def read_preflight(self, qa, directory):
        self.assertEqual(self.phase, "sales")  # no admin session is borrowed for master data
        self.assertEqual(directory.endpoint, "EP-lab")
        return {"status": "ok", "qa": copy.deepcopy(qa), "masters": self.masters,
                "paints": [["paint", "P", "Live paint"]]}

    def submit(self, **kwargs):
        self.assertEqual(self.phase, "sales")
        self.assertIs(kwargs["on_attempt"], self.attempt)
        kwargs["on_attempt"]({"booking_no": "LAB-1"})
        return "external-lab-1", "LAB-1"

    def book(self, **kwargs):
        return service.book_in_session({"id": "EP-lab", "name": "EP-lab"}, self.payload,
                                       on_attempt=self.attempt, **kwargs)

    def edited_customer(self, outcomes):
        """Booking with an edited customer; the dispatch marker's answers are scripted."""
        self.payload["nonce"] = "booking-nonce"
        self.payload["qa"].update(customer_dirty=True, user_id="operator-lab",
                                  customer_baseline={"people_id": "fixture-id", "phone": "0800000001"},
                                  customer_changes={"phone": "0812345678"})
        calls = []

        class Marker:
            def __init__(marker, endpoint, request_id, actor, **kwargs):
                calls.append("init")

            def previous(marker):
                calls.append("previous")
                outcome = outcomes.pop(0)
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome

            def reconcile(marker, client):
                calls.append("reconcile")
                self.assertIs(client, self.client)

            def begin(marker, verify):
                calls.append("begin")

            def finish(marker, result):
                calls.append("finish")
                return result

        self.enterContext(patch("pearnly_dms.intake.customer_attempt.Attempt", Marker))
        self.client.save_customer.side_effect = lambda **kw: (kw["on_attempt"]({"mode": "overwrite"}),
                                                              ("customer-lab", False))[1]
        return calls

    def test_blocked_customer_write_is_checked_in_the_same_session_then_written(self):
        from pearnly_dms.contracts.customer_merge import unknown
        calls = self.edited_customer([unknown(), None])
        self.assertTrue(self.book()["ok"])
        self.assertEqual(calls, ["init", "previous", "reconcile", "previous", "begin", "finish"])
        self.client.create_booking_via_form.assert_called_once()

    def test_checked_as_already_written_is_not_written_again(self):
        from pearnly_dms.contracts.customer_merge import unknown
        calls = self.edited_customer([unknown(), {"success": True, "customer_id": "customer-lab"}])
        self.assertTrue(self.book()["ok"])
        self.assertEqual(calls, ["init", "previous", "reconcile", "previous"])
        self.client.save_customer.assert_not_called()
        self.client.create_booking_via_form.assert_called_once()

    def test_still_unconfirmed_customer_write_stops_before_the_order(self):
        from pearnly_dms.booking.conversation import booking_flow
        from pearnly_dms.contracts.customer_merge import unknown
        calls = self.edited_customer([unknown(), unknown()])
        result = self.book()
        self.assertEqual(result["error_code"], "ERR_DMS_CUSTOMER_UNCONFIRMED")
        self.assertEqual(calls, ["init", "previous", "reconcile", "previous", "finish"])
        self.client.save_customer.assert_not_called()
        self.client.create_booking_via_form.assert_not_called()
        self.attempt.assert_not_called()  # the order was never registered, so retrying is safe
        self.assertTrue(booking_flow._retryable_result(result))

    def test_resolver_mapping_runs_through_defaults_and_single_snapshot(self):
        result = self.book()
        self.assertTrue(result["ok"])
        sent = self.client.create_booking_via_form.call_args.kwargs["booking"]
        self.assertEqual((sent.car.id, sent.car.name, sent.paint.id), ("car", "Live car", "paint"))
        self.assertEqual((sent.delivery_date_be, sent.regis_name), ("22/10/2569", "Edited Registration"))
        self.assertEqual(sent.payments[0]["amount"], "10.25")
        self.client.resolve_booking_payload.assert_not_called()
        self.client.fetch_masters.assert_not_called()
        # 建单成功只回填已确认的客户资料；不自动刷新员工销售全量
        # （见 booking/service 模块头第 4 条），所以这里必须不被调用。
        self.cache.assert_not_called()
        self.attempt.assert_called_once()

    def test_removed_master_never_submits(self):
        self.preflight.side_effect = None
        self.preflight.return_value = {"status": "unmatched", "field": "car", "qa": self.payload["qa"]}
        self.assertEqual(self.book()["preflight"], "unmatched")
        self.client.create_booking_via_form.assert_not_called()
        self.attempt.assert_not_called()

    def test_post_commit_failures_preserve_real_booking_identity(self):
        self.client.attach_booking_files.side_effect = RuntimeError("attachment fixture")
        self.cache.side_effect = RuntimeError("cache fixture")
        result = self.book(attach_files=[{"filename": "fixture.png"}])
        self.assertTrue(result["ok"])
        self.assertEqual((result["booking_id"], result["booking_no"]), ("external-lab-1", "LAB-1"))
        self.assertFalse(result["attach_ok"])
        self.assertFalse(result["cache_updated"])
        self.client.create_booking_via_form.assert_called_once()

    def test_ledger_mapping_never_enters_link_column(self):
        doc = Mock(name="ledger")
        with patch.object(frappe, "new_doc", return_value=doc), \
             patch.object(frappe.db, "get_value", return_value=None):
            booking_ledger.log_booking(endpoint={"id": "EP-lab"}, payload=self.payload,
                                       result={"ok": True, "booking_id": "external-lab-1", "booking_no": "LAB-1"})
        self.assertEqual(doc.endpoint, "EP-lab")
        doc.insert.assert_called_once()

    def test_recovered_success_reuses_existing_actor_endpoint_ledger(self):
        with patch.object(frappe.db, "get_value", return_value="PL-existing") as get, \
             patch.object(frappe, "new_doc") as new:
            name = booking_ledger.log_booking(endpoint={"id": "EP-lab"}, payload=self.payload,
                actor="operator-local", result={"ok": True, "booking_id": "external-lab-1", "booking_no": "LAB-1"})
        self.assertEqual(name, "PL-existing")
        # One order, one row: the "manual" row an unknown result left is the one turned into success.
        self.assertEqual(get.call_args.args[1], {"status": ["in", ["success", "manual"]], "business_key": "LAB-1",
            "endpoint": "EP-lab", "actor": "operator-local"})
        new.assert_not_called()
        self.assertEqual(frappe.db.set_value.call_args.args[2]["status"], "success")


class OCRDispatcherRecovery(unittest.TestCase):
    def test_sql_repair_survives_disabled_general_scheduler_and_one_broken_site(self):
        from pearnly_core.execution import dispatch, work
        with patch('frappe.utils.get_sites', return_value=['broken.localhost', 'healthy.localhost']), \
             patch.object(dispatch, '_cursor', 0), patch.object(dispatch, '_repaired', {}), \
             patch.object(dispatch, '_pruned', {}), \
             patch.object(frappe, 'init'), patch.object(frappe, 'connect', side_effect=[RuntimeError(), None]), \
             patch.object(frappe, 'destroy'), patch.object(frappe, 'set_user'), \
             patch.object(frappe, 'db', Mock()), \
             patch.object(frappe, 'conf', frappe._dict(disable_scheduler=1, pearnly_ocr_budget_redis='redis://local/6')), \
             patch.object(work, 'sweep') as sweep, patch.object(work, 'prune_checkpoints') as prune, \
             patch('builtins.print'):
            dispatch.repair_one()
            dispatch.repair_one()
            dispatch.repair_one()
            dispatch.repair_one()
            sweep.assert_called_once()
            prune.assert_called_once()

    def test_explicit_pause_keeps_durable_work_without_executing_it(self):
        from pearnly_core.execution import work
        with patch.object(frappe, 'conf', frappe._dict(pearnly_ocr_paused=1)), \
             patch.object(frappe, 'db', Mock()) as db:
            work.run('owned-paused-work')
            db.begin.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
