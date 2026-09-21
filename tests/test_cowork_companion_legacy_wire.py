"""Only the optional WSGI envelope seam; no Site or external requests."""
import unittest

from werkzeug.test import Client
from werkzeug.wrappers import Response

from pearnly_cowork.companion.legacy_wire import LegacyAgentWire, ROUTES


class LegacyWireTests(unittest.TestCase):
    def test_only_exact_post_paths_are_rewritten_and_other_responses_are_untouched(self):
        seen = []
        def native(environ, start_response):
            seen.append((environ['PATH_INFO'], environ['REQUEST_METHOD']))
            return Response('{"message":{"ok":true,"jobs":[]}}', content_type='application/json',
                            headers={'Set-Cookie': 'sid=synthetic'})(environ, start_response)
        client = Client(LegacyAgentWire(native), Response, use_cookies=False)
        for path, target in ROUTES.items():
            result = client.post(path)
            self.assertEqual(seen[-1], (target, 'POST'))
            self.assertEqual(result.json, {'ok': True, 'jobs': []})
            self.assertEqual(result.headers.getlist('Set-Cookie'), [])
            for method, other in [('GET', path), ('POST', path + '/'), ('POST', target),
                                  ('POST', '/api/method/ping')]:
                result = client.open(other, method=method)
                self.assertEqual(seen[-1], (other, method))
                self.assertIn('message', result.json)
                self.assertEqual(result.headers.getlist('Set-Cookie'), ['sid=synthetic'])

    def test_invalid_success_cannot_be_mistaken_for_empty_queue_or_successful_ack(self):
        for raw in ('{}', 'null', '[]', '{"message":{"ok":false}}', 'not json',
                    '{"message":{"ok":true},"exc":"private-upstream-detail"}',
                    '{"message":{"ok":true,"number":NaN}}'):
            with self.subTest(raw=raw):
                upstream = Response(raw, content_type='application/json')
                result = Client(LegacyAgentWire(upstream), Response).post('/api/erp/agent/lease')
                self.assertEqual(result.status_code, 502)
                self.assertEqual(result.json, {'error': 'companion_wire_invalid_response'})
                self.assertIn('no-store', result.headers['Cache-Control'])

    def test_native_failure_is_not_retried_and_response_is_closed(self):
        calls, closed = [], []
        def native(environ, start_response):
            calls.append(True)
            result = Response('{"error":"synthetic-unavailable"}', status=503,
                              content_type='application/json', headers={'Set-Cookie': 'sid=synthetic'})
            result.call_on_close(lambda: closed.append(True))
            return result(environ, start_response)
        result = Client(LegacyAgentWire(native), Response).post('/api/erp/agent/ack')
        self.assertEqual((len(calls), len(closed)), (1, 1))
        self.assertEqual(result.status_code, 503)
        self.assertEqual(result.json, {'error': 'synthetic-unavailable'})
        self.assertEqual(result.headers.getlist('Set-Cookie'), [])


if __name__ == '__main__':
    unittest.main()
