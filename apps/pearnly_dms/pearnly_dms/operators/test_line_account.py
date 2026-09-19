"""Assigned OA metadata only; HTTP is mocked, no LINE messages or Site writes."""
import base64
import unittest
from unittest.mock import patch

import frappe
import requests

from pearnly_dms.operators import line_account


class AccountUnavailable(Exception):
    pass


class TestPublicLineAccount(unittest.TestCase):
    def setUp(self):
        self.config = {
            'pearnly_line_channel_access_token': 'fixture-token-a',
            'pearnly_line_channel_secret': 'fixture-secret',
            'pearnly_line_channel_id': '1234567890',
        }
        self.enterContext(patch.object(line_account.frappe, 'conf', self.config))
        self.enterContext(patch.object(line_account, '_', lambda key: key))
        self.enterContext(patch.object(line_account.frappe, 'throw', side_effect=AccountUnavailable))
        self.http = self.enterContext(patch.object(line_account.requests, 'get'))
        self.http.return_value.json.return_value = {
            'basicId': '@assigned-company', 'displayName': 'Company LINE',
            'userId': 'private-bot-user-id',
        }

    def test_public_metadata_matches_current_sites_token(self):
        for token, basic_id in [('fixture-token-a', '@company-a'), ('fixture-token-b', '@company-b')]:
            with self.subTest(site_token=token):
                self.config['pearnly_line_channel_access_token'] = token
                self.http.return_value.json.return_value['basicId'] = basic_id
                with patch.object(line_account.pyqrcode, 'create', wraps=line_account.pyqrcode.create) as qr:
                    result = line_account.public_account()
                self.http.assert_called_with('https://api.line.me/v2/bot/info',
                    headers={'Authorization': 'Bearer ' + token}, timeout=10)
                self.assertEqual(set(result), {'name', 'basic_id', 'url', 'qr'})
                self.assertEqual(result['basic_id'], basic_id)
                self.assertEqual(result['url'], 'https://line.me/R/ti/p/%40' + basic_id[1:])
                qr.assert_called_once_with(result['url'])
                svg = base64.b64decode(result['qr'].split(',', 1)[1]).decode()
                self.assertIn('<svg', svg)
                self.assertIn('fill="white"', svg)
                self.assertNotIn(token, str(result))
                self.assertNotIn('private-bot-user-id', str(result))

    def test_missing_configuration_never_calls_line(self):
        for key in ['pearnly_line_channel_access_token', 'pearnly_line_channel_secret']:
            with self.subTest(key=key), patch.dict(self.config, {key: ''}):
                with self.assertRaises(AccountUnavailable):
                    line_account.public_account()
                frappe.throw.assert_called_with('roster.line.notConfigured')
        self.http.assert_not_called()

    def test_invalid_response_uses_localized_failure(self):
        for info in [None, [], 'unexpected', {}, {'basicId': '1234567890'}]:
            with self.subTest(info=info):
                self.http.return_value.json.return_value = info
                with self.assertRaises(AccountUnavailable):
                    line_account.public_account()
                frappe.throw.assert_called_with('roster.line.unavailable')

    def test_failed_lookup_uses_localized_failure(self):
        for error in [requests.Timeout('offline'), requests.HTTPError('unauthorized'), ValueError('not json')]:
            with self.subTest(error=type(error).__name__):
                self.http.side_effect = error
                with self.assertRaises(AccountUnavailable):
                    line_account.public_account()
                frappe.throw.assert_called_with('roster.line.unavailable')
