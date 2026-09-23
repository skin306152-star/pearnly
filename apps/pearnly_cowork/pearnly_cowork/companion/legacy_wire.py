"""Optional WSGI transport for the three fixed Companion 1.1.77 POST paths.

Only the URL and successful JSON envelope change. Native Frappe still owns
authentication, request parsing, transaction commit, permissions and errors.
No endpoint, token, actor, payload or business result is invented here.
"""
import json

from werkzeug.wrappers import Response

ROUTES = {
    '/api/erp/agent/heartbeat': '/api/method/pearnly_cowork.companion.heartbeat.report',
    '/api/erp/agent/lease': '/api/method/pearnly_cowork.companion.queue_api.lease',
    '/api/erp/agent/ack': '/api/method/pearnly_cowork.companion.queue_api.acknowledge',
}


class LegacyAgentWire:
    def __init__(self, native_application):
        self.native_application = native_application

    def __call__(self, environ, start_response):
        target = ROUTES.get(environ.get('PATH_INFO'))
        if not target or environ.get('REQUEST_METHOD') != 'POST':
            return self.native_application(environ, start_response)
        request = {**environ, 'PATH_INFO': target}
        # Consume/close the native response exactly once, after its existing
        # commit and cleanup boundary. No retry on errors or lost responses.
        response = Response.from_app(self.native_application, request, buffered=True)
        if response.status_code == 200:
            try:
                body = response.get_json()
                value = body.get('message') if isinstance(body, dict) else None
                if (not isinstance(value, dict) or value.get('ok') is not True
                        or any(body.get(key) for key in ('exc', 'exception', 'exc_type'))):
                    raise ValueError('invalid native envelope')
                encoded = json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
            except (ValueError, TypeError):
                # A shape mismatch must not look like an empty queue or a
                # successful ACK. Do not echo upstream data in this error.
                response = Response('{"error":"companion_wire_invalid_response"}', status=502,
                                    content_type='application/json')
            else:
                response.set_data(encoded)
                response.content_type = 'application/json'
                for key in ('ETag', 'Content-MD5', 'Content-Encoding'):
                    response.headers.remove(key)
        # The native machine hooks already enforce these, including errors.
        # Repeat at the external envelope seam so it cannot set browser state.
        response.headers.remove('Set-Cookie')
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Pragma'] = 'no-cache'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response(environ, start_response)
