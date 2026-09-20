"""Private local SMTP sink for synthetic example.test identities; never forwards."""
from pathlib import Path
from socketserver import StreamRequestHandler, ThreadingTCPServer
import uuid

ROOT = Path('/lab-mail')
ROOT.mkdir(exist_ok=True)


class Handler(StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(30)
        accepted = False

        def reply(line):
            self.wfile.write((line + '\r\n').encode())
            self.wfile.flush()

        reply('220 line-lab ESMTP')
        while True:
            raw = self.rfile.readline(8192)
            if not raw:
                return
            command = raw.decode(errors='replace').strip()
            verb = command.split(' ', 1)[0].upper()
            if verb in ('EHLO', 'HELO'):
                reply('250-line-lab')
                reply('250 SIZE 2097152')
            elif verb == 'MAIL':
                accepted = False
                reply('250 OK')
            elif verb == 'RCPT':
                recipient = command.split(':', 1)[-1].strip().strip('<>').lower()
                accepted = recipient.endswith('@example.test')
                reply('250 OK' if accepted else '550 Synthetic example.test recipients only')
            elif verb == 'DATA' and accepted:
                reply('354 End with dot')
                lines, size = [], 0
                while True:
                    line = self.rfile.readline(8192)
                    if line in (b'.\r\n', b'.\n'):
                        break
                    size += len(line)
                    if not line or size > 2097152:
                        reply('552 Message too large or incomplete')
                        return
                    lines.append(line[1:] if line.startswith(b'..') else line)
                path = ROOT / (uuid.uuid4().hex + '.eml')
                path.write_bytes(b''.join(lines))
                path.chmod(0o600)
                reply('250 Stored locally')
            elif verb == 'RSET':
                accepted = False
                reply('250 OK')
            elif verb == 'NOOP':
                reply('250 OK')
            elif verb == 'QUIT':
                reply('221 Bye')
                return
            else:
                reply('502 Unsupported')


class Server(ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


with Server(('0.0.0.0', 2525), Handler) as server:
    server.serve_forever()
