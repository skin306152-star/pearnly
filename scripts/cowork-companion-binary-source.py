"""Read-only PyInstaller Python-code/source comparison; never execute the EXE.

Use a trusted, separately hashed installed binary and the same CPython minor.
This proves Python business-code correspondence, not build provenance, native
helpers, dependency integrity, or ERP behavior. Neither input repository changes.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import marshal
from pathlib import Path
import re
import struct
import subprocess
import sys
import types
import zlib

CODE_FIELDS = ('co_argcount', 'co_posonlyargcount', 'co_kwonlyargcount', 'co_nlocals',
    'co_stacksize', 'co_flags', 'co_code', 'co_consts', 'co_names', 'co_varnames',
    'co_freevars', 'co_cellvars', 'co_firstlineno', 'co_name', 'co_qualname',
    'co_linetable', 'co_exceptiontable')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def normalized(value):
    if isinstance(value, types.CodeType):
        return {key: normalized(getattr(value, key)) for key in CODE_FIELDS}
    if isinstance(value, bytes):
        return {'bytes': value.hex()}
    if isinstance(value, (tuple, list, frozenset)):
        parts = [normalized(item) for item in value]
        return {type(value).__name__: sorted(parts, key=repr) if isinstance(value, frozenset) else parts}
    return {type(value).__name__: repr(value)}


def package(data):
    magic, cookie_format = b'MEI\014\013\012\013\016', '!8sIIII64s'
    end, size = data.rfind(magic), struct.calcsize(cookie_format)
    require(end >= 0 and end + size <= len(data), 'Missing CArchive cookie')
    _, length, offset, toc_length, version, library = struct.unpack(cookie_format, data[end:end + size])
    start = end + size - length
    require(start >= 0 and offset + toc_length <= length - size, 'Invalid archive bounds')
    require(version == sys.version_info.major * 100 + sys.version_info.minor,
            'Run with the installed binary CPython minor before reading marshal')
    toc, entries, position = data[start + offset:start + offset + toc_length], {}, 0
    while position < len(toc):
        require(position + 18 <= len(toc), 'Truncated archive entry')
        size, entry_offset, compressed_length, raw_length, compressed, kind = struct.unpack(
            '!iIIIBc', toc[position:position + 18])
        require(18 < size <= len(toc) - position and entry_offset + compressed_length <= offset,
                'Invalid archive entry bounds')
        name = toc[position + 18:position + size].rstrip(b'\0').decode()
        require(name not in entries and compressed in (0, 1), 'Invalid archive entry')
        entries[name] = (entry_offset, compressed_length, raw_length, compressed, kind)
        position += size

    def read(name):
        entry_offset, length, raw_length, compressed, _ = entries[name]
        content = data[start + entry_offset:start + entry_offset + length]
        content = zlib.decompress(content) if compressed else content
        require(len(content) == raw_length, 'Archive entry length mismatch')
        return content

    pyzs = [name for name in entries if name.endswith('.pyz')]
    require(len(pyzs) == 1, 'Expected one PYZ')
    pyz = read(pyzs[0])
    require(pyz[:4] == b'PYZ\0' and pyz[4:8] == importlib.util.MAGIC_NUMBER, 'PYZ Python mismatch')
    toc_offset = struct.unpack('!i', pyz[8:12])[0]
    require(12 <= toc_offset < len(pyz), 'Invalid PYZ table')
    modules, codes = dict(marshal.loads(pyz[toc_offset:])), {}
    for name, (_, offset, length) in modules.items():
        if name == 'src.companion' or name.startswith('src.companion.'):
            require(12 <= offset and offset + length <= toc_offset, 'Invalid module bounds')
            codes[name] = marshal.loads(zlib.decompress(pyz[offset:offset + length]))
    require(codes and 'src.companion.version' in codes, 'Missing Companion modules')
    require(entries['launcher_companion'][-1] == b's', 'Missing launcher script')
    codes['__launcher__'] = marshal.loads(read('launcher_companion'))
    require(all(isinstance(code, types.CodeType) for code in codes.values()), 'Expected code objects')
    return codes, version, library.rstrip(b'\0').decode()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe', type=Path, required=True)
    parser.add_argument('--expected-sha256', required=True)
    parser.add_argument('--source-repo', type=Path, required=True)
    parser.add_argument('--source-sha', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    require(re.fullmatch(r'[0-9a-f]{40}', args.source_sha), 'Full fixed Git commit required')
    require(re.fullmatch(r'[0-9a-f]{64}', args.expected_sha256), 'Expected SHA256 required')
    resolved = subprocess.check_output(['git', '-C', str(args.source_repo), 'rev-parse',
        args.source_sha + '^{commit}'], text=True).strip()
    require(resolved == args.source_sha, 'Source commit mismatch')
    require(args.exe.stat().st_size <= 200 * 1024 * 1024, 'Unexpected executable size')
    data = args.exe.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    require(digest == args.expected_sha256, 'Installed executable hash changed')
    codes, version, library = package(data)
    results = []
    for name, code in sorted(codes.items()):
        path = ('packaging/launcher_companion.py' if name == '__launcher__' else
                'src/companion/__init__.py' if name == 'src.companion' else name.replace('.', '/') + '.py')
        source = subprocess.check_output(['git', '-C', str(args.source_repo), 'show', args.source_sha + ':' + path])
        actual, expected = normalized(code), normalized(compile(source, path, 'exec', dont_inherit=True, optimize=0))
        results.append({'module': name, 'path': path, 'source_sha256': hashlib.sha256(source).hexdigest(),
            'match': actual == expected, 'different_keys': [key for key in actual if actual[key] != expected[key]]})
    output = {'captured_at': datetime.now(timezone.utc).isoformat(), 'exe_sha256': digest,
        'source_commit': args.source_sha, 'python_version': version, 'python_library': library,
        'auditor_python': sys.version.split()[0], 'embedded_version_constants':
            [value for value in codes['src.companion.version'].co_consts if isinstance(value, str)],
        'normalization': 'Only co_filename excluded recursively; all other listed code fields and typed constants retained',
        'code_fields': CODE_FIELDS, 'modules': results, 'matched': sum(row['match'] for row in results),
        'total': len(results), 'executed_packaged_code': False,
        'limitations': ['No signed build provenance', 'Native helpers and dependencies not compared',
                       'No ERP business result']}
    args.output.write_text(json.dumps(output, indent=2, ensure_ascii=False) + '\n')
    print(json.dumps({'matched': output['matched'], 'total': output['total'], 'exe_sha256': digest}))
    return 0 if output['matched'] == output['total'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
