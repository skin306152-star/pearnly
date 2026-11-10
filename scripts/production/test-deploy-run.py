"""Offline tests for the one-command production deployment: no Docker, GitHub, SSH or host changes.

deploy-run.sh (server side) must keep the manual order and every gate: revision, tooling sync and check,
products one at a time behind the 8 GB memory wait, foundation, readback; stop at the first failure.
deploy.sh (local driver) must refuse a commit whose flow cases are not green on GitHub (status flows, run against
the last staged commit or earlier), then chain build → stage → one SSH start → one long SSH attach, and stop with
guidance when a step fails. deploy-readback.sh must flag every readback item that is off.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

HERE = Path(__file__).parent
RUNNER = HERE / 'deploy-run.sh'
READBACK = HERE / 'deploy-readback.sh'
DRIVER = HERE / 'deploy.sh'
SHA = 'f' * 40
BASE = 'a' * 40   # 首父链上更早、有全套的提交（沿用用）
DIGEST = 'sha256:' + 'e' * 64
FLOWS_OK = 'success all flows; 1 flows; datasets new,old; 4/4 runs passed; 812s'
IMAGE = 'ghcr.io/skin306152-star/pearnly/frappe@' + DIGEST
TAG = 'release-20261031-1405'  # 已有的发布标记（--tag 用）


def stub(path, body):
    path.write_text('#!/usr/bin/env bash\n' + body)
    path.chmod(0o755)


class Runner(unittest.TestCase):
    def run_runner(self, revision=SHA, fail_product='', memory=(30000,), lock_pid=None, config_exit=0):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            state, deploy, bin_dir = root / 'state', root / 'deploy', root / 'bin'
            for d in (state, deploy / 'scripts/production', bin_dir):
                d.mkdir(parents=True)
            trace = root / 'trace'
            # Server copy inside the "image": the runner itself plus fake activation / readback scripts.
            runner_copy = deploy / 'scripts/production/deploy-run.sh'
            runner_copy.write_text(RUNNER.read_text())
            for name in ('activate-product.sh', 'activate-foundation.sh', 'deploy-readback.sh'):
                (deploy / 'scripts/production' / name).write_text(
                    f'echo "{name} $*" >> "{trace}"\n'
                    f'[ "{name} $2" != "activate-product.sh {fail_product}" ] || {{ echo boom; exit 4; }}\n'
                    f'echo "done {name}"\n')
            (deploy / 'scripts/production/check-deploy-image.py').write_text(
                f'import sys\nopen("{trace}", "a").write("check-deploy-image " + " ".join(sys.argv[1:]) + "\\n")\n')
            # 租户配置对比本身另有离线测试（test-check-tenant-config.py）；这里只看它在激活前跑、交给它镜像里的地图导出
            (deploy / 'scripts/production/check-tenant-config.py').write_text(
                f'import sys\nopen("{trace}", "a").write("check-tenant-config " + " ".join(sys.argv[1:]) + "\\n")\n'
                f'print("  BLOCK erp mrerp.pearnly.com pearnly_x" if {config_exit} else "  tenant config check passed")\n'
                f'sys.exit({config_exit})\n')
            stub(bin_dir / 'docker', f'echo "docker $*" >> "{trace}"\n'
                 f'if [ "$1 $2" = "image inspect" ]; then echo {revision}; fi\n')
            readings = root / 'memory'
            readings.write_text('\n'.join(str(m) for m in memory) + '\n')
            # free: hand out the next memory reading for each wait check; sampler calls also consume (harmless).
            stub(bin_dir / 'free', f'm=$(head -1 "{readings}"); [ $(wc -l < "{readings}") -gt 1 ] && sed -i.bak 1d "{readings}"\n'
                 'echo "              total        used        free      shared  buff/cache   available"\n'
                 'echo "Mem:          32000        2000       1000          0       1000       $m"\n')
            if lock_pid is not None:
                (state / 'deploy.lock').mkdir()
                (state / 'deploy.lock/pid').write_text(f'{lock_pid}\n')
            env = dict(os.environ, PATH=f'{bin_dir}{os.pathsep}{os.environ["PATH"]}',
                       PEARNLY_STATE_DIR=str(state), PEARNLY_DEPLOY_ROOT=str(deploy),
                       PEARNLY_DEPLOY_MEM_WAIT_SECONDS='0')
            result = subprocess.run(['bash', str(runner_copy), IMAGE, SHA], env=env, text=True,
                                    capture_output=True, timeout=60)
            short = SHA[:8]
            read = lambda p: p.read_text() if p.exists() else ''
            return (result, read(trace).splitlines(), read(state / f'deploy-{short}.status').strip(),
                    read(state / f'deploy-{short}.log'), (state / 'deploy.lock').exists())

    def test_success_keeps_manual_order_and_every_gate(self):
        result, trace, status, log, locked = self.run_runner()
        self.assertEqual(result.returncode, 0, log)
        self.assertEqual(status, 'ok')
        steps = [line.split()[0] if not line.startswith('docker') else ' '.join(line.split()[:2]) for line in trace]
        self.assertEqual([s for s in steps if s != 'docker rm'], [
            'docker ps', 'docker image', 'docker create', 'docker cp', 'check-deploy-image', 'check-tenant-config',
            'activate-product.sh', 'activate-product.sh', 'activate-product.sh',
            'activate-foundation.sh', 'deploy-readback.sh'])
        products = [line.split()[2] for line in trace if line.startswith('activate-product.sh')]
        self.assertEqual(products, ['erp', 'cowork', 'dms'])
        self.assertIn('DEPLOY DONE', log)
        self.assertFalse(locked, 'lock is released after the run')

    def test_tenant_config_is_checked_with_the_image_map_before_activation(self):
        result, trace, status, log, _ = self.run_runner()
        check = next(line for line in trace if line.startswith('check-tenant-config'))
        self.assertTrue(check.endswith('/scripts/production/product-map.json'), check)
        result, trace, status, log, locked = self.run_runner(config_exit=1)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(status, 'failed config')
        self.assertIn('BLOCK erp mrerp.pearnly.com', log)
        self.assertFalse([l for l in trace if l.startswith('activate')])
        self.assertFalse(locked)

    def test_wrong_revision_stops_before_anything_changes(self):
        result, trace, status, log, _ = self.run_runner(revision='0' * 40)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(status, 'failed revision')
        self.assertFalse([l for l in trace if l.startswith(('docker cp', 'activate'))])

    def test_product_failure_stops_later_products(self):
        result, trace, status, log, locked = self.run_runner(fail_product='cowork')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(status, 'failed cowork')
        products = [line.split()[2] for line in trace if line.startswith('activate-product.sh')]
        self.assertEqual(products, ['erp', 'cowork'])
        self.assertFalse([l for l in trace if l.startswith(('activate-foundation', 'deploy-readback'))])
        self.assertIn('boom', log)
        self.assertFalse(locked)

    def test_waits_for_memory_before_each_product(self):
        # The sampler and the 'free before' line read memory too (at most 2 reads before the first wait check),
        # so hand out four low readings: the first wait check always sees one of them.
        result, trace, status, log, _ = self.run_runner(memory=(5000, 5000, 5000, 5000, 30000))
        self.assertEqual(status, 'ok', log)
        self.assertRegex(log, r'available \d+ MB < 8192, waiting')

    def test_memory_that_never_recovers_stops_before_activation(self):
        result, trace, status, log, _ = self.run_runner(memory=(4000,))
        self.assertEqual(status, 'failed erp')
        self.assertIn('memory did not recover', log)
        self.assertFalse([l for l in trace if l.startswith('activate')])

    def test_running_deployment_holds_the_lock(self):
        result, trace, status, log, locked = self.run_runner(lock_pid=os.getpid())
        self.assertEqual(result.returncode, 3)
        self.assertEqual(trace, [])
        self.assertTrue(locked, 'the other deployment keeps its lock')

    def test_stale_lock_is_taken_over(self):
        dead = subprocess.Popen(['true']); dead.wait()
        result, trace, status, log, _ = self.run_runner(lock_pid=dead.pid)
        self.assertEqual(status, 'ok', log)


class RunnerSelfCheck(unittest.TestCase):
    def test_uploaded_runner_differs_from_image_copy(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            state, deploy, bin_dir = root / 'state', root / 'deploy', root / 'bin'
            for d in (state, deploy / 'scripts/production', bin_dir):
                d.mkdir(parents=True)
            uploaded = state / 'deploy-run-ffffffff.sh'
            uploaded.write_text(RUNNER.read_text() + '# not the image copy\n')
            (deploy / 'scripts/production/deploy-run.sh').write_text(RUNNER.read_text())
            (deploy / 'scripts/production/check-deploy-image.py').write_text('')
            stub(bin_dir / 'docker', f'if [ "$1 $2" = "image inspect" ]; then echo {SHA}; fi\n')
            stub(bin_dir / 'free', 'echo "Mem: 1 1 1 1 1 30000"\n')
            env = dict(os.environ, PATH=f'{bin_dir}{os.pathsep}{os.environ["PATH"]}',
                       PEARNLY_STATE_DIR=str(state), PEARNLY_DEPLOY_ROOT=str(deploy))
            result = subprocess.run(['bash', str(uploaded), IMAGE, SHA], env=env, text=True,
                                    capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 1)
            self.assertEqual((state / 'deploy-ffffffff.status').read_text().strip(), 'failed sync-tooling')
            self.assertIn('not the copy inside the image', (state / 'deploy-ffffffff.log').read_text())


class Readback(unittest.TestCase):
    def run_readback(self, containers=None, maintenance=0, errors=0, codes=None, record=IMAGE, rejected='NULL', browser='NULL',
                     browser_before=0, liff='2010411313-aaaa1111', liff_exit=0, liff_seen=None, entries_exit=0,
                     export=True, unmarked=(), bench_max=42991616, nginx_mib=41, upload_code=403):
        containers = containers or {
            'pearnly-production-erp-web-1': IMAGE, 'pearnly-production-erp-ocr-worker-1': IMAGE,
            'pearnly-production-mariadb-1': 'mariadb:11', 'pearnly-production-billing-web-1': IMAGE}
        codes = codes or {}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            bin_dir, deploy = root / 'bin', root / 'deploy'
            bin_dir.mkdir(); deploy.mkdir()
            # LIFF 检查本身另有离线测试（test-check-liff-endpoints.py）；这里换成假的，只看回读交给它什么、认不认它的结果。
            (deploy / 'scripts/production').mkdir(parents=True)
            (deploy / 'scripts/production/check-liff-endpoints.py').write_text(
                f'import sys\nopen("{root}/liff-stdin", "w").write(sys.stdin.read() + " ".join(sys.argv[1:]))\n'
                f'print("  fake liff check")\nsys.exit({liff_exit})\n')
            # 逐租户入口检查另有离线测试（test-check-tenant-entries.py）；这里看回读交给它地图导出、认不认它的结果
            (deploy / 'scripts/production/check-tenant-entries.py').write_text(
                f'import sys\nprint("  fake entries check " + " ".join(sys.argv[1:]))\nsys.exit({entries_exit})\n')
            if export:
                (deploy / 'scripts/production/product-map.json').write_text('{"version": 1, "config": {}, "rows": []}\n')
            for product in ('erp', 'cowork', 'dms'):
                (deploy / f'active-image-{product}.json').write_text(json.dumps([record]) + '\n')
            (root / 'containers.json').write_text(json.dumps(containers))
            (root / 'unmarked.json').write_text(json.dumps(list(unmarked)))  # 这几个容器没有 PEARNLY_DEPLOYMENT=production
            stub(bin_dir / 'docker', f'''python3 - "$@" <<'EOF'
import json, sys
args = sys.argv[1:]
containers = json.load(open("{root}/containers.json"))
if args[0] == "ps":
    print("\\n".join(list(containers) + ["pearnly-maintenance-erp"]))
elif args[0] == "inspect" and "Config.Image" in args[2]:
    print(containers[args[3]])
elif args[0] == "inspect":
    unmarked = json.load(open("{root}/unmarked.json"))
    print("PATH=/usr/bin\\nFRAPPE_BACKGROUND_WORKERS_NOFORK=1" + ("" if args[-1] in unmarked else "\\nPEARNLY_DEPLOYMENT=production"))
elif args[0] == "exec" and "python3" in args:
    product = args[3].split("-")[2]
    print(f"site-a-{{product}}.pearnly.com {maintenance} {liff}\\nsite-b-{{product}}.pearnly.com 0 -")
elif args[0] == "exec" and "UPLOAD_MAX_BYTES" in args[-1]:
    print("41943040 42991616 {bench_max}")
elif args[0] == "exec" and "time_zone" in args[-1]:
    print("Asia/Bangkok")
elif args[0] == "exec" and "sum(error" in args[-1]:
    print("{errors} {rejected} {browser} {browser_before}")
elif args[0] == "exec":
    print("2026-10-28 10:00:00 Traceback sample")
elif args[0] == "stats":
    print("pearnly-production-mariadb-1 873MiB / 8GiB")
EOF
''')
            stub(bin_dir / 'curl', f'''url="${{@: -1}}"
case "$url" in {''.join(f'{u}) printf {c} ;; ' for u, c in codes.items())}*assistant.api.upload) printf {upload_code} ;; *) printf 200 ;; esac
''')
            nginx = root / 'nginx'
            nginx.mkdir()
            for site in ('testcowork.pearnly.com', 'erpcowork.pearnly.com'):
                (nginx / f'pearnly-tenant-{site}.conf').write_text(f'server {{ client_max_body_size {nginx_mib}m; }}\n')
            state = root / 'state'
            state.mkdir()
            env = dict(os.environ, PATH=f'{bin_dir}{os.pathsep}{os.environ["PATH"]}', PEARNLY_DEPLOY_ROOT=str(deploy),
                       PEARNLY_DEPLOY_PING_WAIT_SECONDS='0', PEARNLY_STATE_DIR=str(state), PEARNLY_NGINX_CONF_DIR=str(nginx))
            result = subprocess.run(['bash', str(READBACK), IMAGE, '1761600000'], env=env, text=True,
                                    capture_output=True, timeout=60)
            marker = state / 'last-readback-ok'
            result.readback_ok = marker.read_text().strip() if marker.exists() else ''
            if liff_seen is not None:
                seen = root / 'liff-stdin'
                liff_seen.append(seen.read_text() if seen.exists() else None)
            return result

    def test_every_upload_layer_takes_one_file_of_the_upload_limit(self):
        # 11-10 压测：Frappe 没配 max_file_size 只收 25MB，CC 一份 27.7MB 的 50 页扫描件被回 413
        clean = self.run_readback()
        self.assertEqual(clean.returncode, 0, clean.stdout + clean.stderr)
        self.assertIn('testcowork.pearnly.com upload of 41943040 bytes as a guest: HTTP 403', clean.stdout)
        self.assertNotIn('erpcowork.pearnly.com upload of', clean.stdout, '正式租户不探')
        for kwargs, said in (({'bench_max': 0}, 'bench max_file_size=0, expected 42991616'),
                             ({'nginx_mib': 40}, 'pearnly-tenant-erpcowork.pearnly.com.conf client_max_body_size 40m < 41m'),
                             ({'upload_code': 413}, 'testcowork.pearnly.com refused a 41943040-byte upload body with HTTP 413')):
            result = self.run_readback(**kwargs)
            self.assertEqual(result.returncode, 1, kwargs)
            self.assertIn(said, result.stdout + result.stderr)

    def test_clean_readback_passes(self):
        result = self.run_readback()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertRegex(result.readback_ok, r'^\d{10}$')   # 回读通过记下时间（部署前列出之后手工动过的容器）
        self.assertEqual(self.run_readback(errors=3).readback_ok, '')
        self.assertIn('Readback passed.', result.stdout)
        self.assertIn('873MiB', result.stdout)
        self.assertIn('pearnly-maintenance-erp', result.stdout)

    def test_every_frappe_container_is_checked_for_the_deployment_mark(self):
        result = self.run_readback()
        self.assertIn('  3 containers checked', result.stdout)   # 两个 web、一个 ocr-worker；数据库不算
        missing = self.run_readback(unmarked=('pearnly-production-billing-web-1',))
        self.assertIn('PROBLEM pearnly-production-billing-web-1 has no PEARNLY_DEPLOYMENT=production', missing.stdout)

    def test_scheduler_rejected_in_maintenance_is_listed_not_a_problem(self):
        result = self.run_readback(errors='NULL', rejected=4)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn('maintenance_rejected_jobs=4', result.stdout)

    def test_browser_error_reports_are_listed_per_tenant(self):
        result = self.run_readback(errors=2, browser=2, browser_before=5)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn('browser_errors_since=2 browser_errors_24h_before=5', result.stdout)
        clean = self.run_readback(browser_before=5)
        self.assertEqual(clean.returncode, 0, clean.stdout)
        self.assertIn('site-a-erp.pearnly.com maintenance=0 errors_since=0 maintenance_rejected_jobs=0 browser_errors_since=0', clean.stdout)

    def test_must_verify_entries_run_after_the_liff_check_with_the_image_map(self):
        result = self.run_readback()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn('-- product map: must-verify entries on every formal tenant', result.stdout)
        self.assertRegex(result.stdout, r'fake entries check --map \S+/scripts/production/product-map.json')
        self.assertLess(result.stdout.index('fake liff check'), result.stdout.index('fake entries check'))

    def test_liff_check_gets_every_site_with_a_liff_and_its_product(self):
        seen = []
        result = self.run_readback(liff_seen=seen)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn('-- LINE: LIFF endpoints and entries', result.stdout)
        self.assertEqual(seen, ['erp site-a-erp.pearnly.com 2010411313-aaaa1111\n'
                                'cowork site-a-cowork.pearnly.com 2010411313-aaaa1111\n'
                                'dms site-a-dms.pearnly.com 2010411313-aaaa1111\n--stdin'])

    def test_each_problem_is_flagged(self):
        cases = {
            'old image': dict(containers={'pearnly-production-dms-web-1': 'ghcr.io/skin306152-star/pearnly/frappe@sha256:' + '1' * 64}),
            'record': dict(record='ghcr.io/skin306152-star/pearnly/frappe@sha256:' + '2' * 64),
            'maintenance': dict(maintenance=1),
            'errors': dict(errors=3),
            'ping': dict(codes={'https://fatcc.com/': 502}),
            'liff endpoint or entry': dict(liff_exit=1),
            'no site has a liff': dict(liff='-'),
            'must-verify entry failed': dict(entries_exit=1),
            'no product map export': dict(export=False),
            # 生产上漏了 PEARNLY_DEPLOYMENT 的容器：CC 只用已有的生产智能体、不建不改，回读要报出来（11-07）
            'a worker without PEARNLY_DEPLOYMENT': dict(unmarked=('pearnly-production-erp-ocr-worker-1',)),
        }
        for name, kwargs in cases.items():
            with self.subTest(case=name):
                result = self.run_readback(**kwargs)
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn('PROBLEM', result.stdout)


class Driver(unittest.TestCase):
    """deploy.sh with fake git / gh / ssh: the order of calls and where it stops."""

    def run_driver(self, *args, build='success', stage='success', remote_status='ok', candidate='success',
                   busy_server=False, attach_failures=0, flows=FLOWS_OK, base_flows='', changed='', on_main=True,
                   with_map=False, config_rc=0, liff_rc=0, dump=True, manual='', external=False, external_rc=0,
                   external_facts=True):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repo, bin_dir = root / 'repo', root / 'bin'
            (repo / 'scripts/production').mkdir(parents=True)
            bin_dir.mkdir()
            for f in (DRIVER, RUNNER, READBACK, HERE / 'target.py', HERE / 'flows_inherit.py'):
                (repo / 'scripts/production' / f.name).write_text(f.read_text())
            if with_map:  # 这个提交带产品地图：部署前配置对比要跑（git archive 交出地图与对比脚本）
                shutil.copytree(HERE.parent / 'impact', repo / 'scripts/impact')
                shutil.copy(HERE.parent / 'check-product-map.py', repo / 'scripts/check-product-map.py')
                shutil.copy(HERE / 'check-tenant-config.py', repo / 'scripts/production/check-tenant-config.py')
                # LIFF 检查本身另有离线测试；这里换成假的（不连外网），只看交给它哪些站点、认不认它的结果
                (repo / 'scripts/production/check-liff-endpoints.py').write_text(
                    f'import sys\nopen("{root}/liff-rows", "w").write(sys.stdin.read())\n'
                    f'print("  PROBLEM mrerp.pearnly.com: LIFF endpoint is /stocktake" if {liff_rc} else "  liff ok")\nsys.exit({liff_rc})\n')
            if external:  # 这个提交带站外设置对比：生产机段经同一次 SSH 传上去，本机比较换成假的（真比较另有离线测试）
                (repo / 'ext/scripts/production').mkdir(parents=True)
                shutil.copy(HERE / 'external-config-collect.py', repo / 'ext/scripts/production/external-config-collect.py')
                (repo / 'ext/scripts/production/check-external-config.py').write_text(
                    f'import shutil, sys\nshutil.copy(sys.argv[sys.argv.index("--facts") + 1], "{root}/external-facts")\n'
                    f'print("  BLOCK [ext:fatcc.com] erp mrerp.pearnly.com: no registration" if {external_rc} else "  站外设置对比通过")\n'
                    f'sys.exit({external_rc})\n')
            (repo / 'scripts/flows').mkdir(parents=True)
            (repo / 'scripts/flows/coverage.py').write_text(
                'import sys\nif "--deploy-note" in sys.argv: print("本批改到的存量模块（还没有流程兜底）1 个：")\n')
            trace = root / 'trace'
            # 发布标记：已有的只有 TAG（与本次脚本自己打、推上去的）；--is-ancestor 按 on_main 回
            stub(bin_dir / 'git', f'''echo "git $*" >> "{trace}"
case "$*" in
  *"--is-ancestor"*) {'exit 0' if on_main else 'exit 1'} ;;
  *"tag -l"*) echo {TAG} ;;
  *"tag -a"*) printf '%s' "$*" | sed 's/.* tag -a \\([^ ]*\\).*/\\1/' > "{root}/made-tag" ;;
  *"--verify refs/tags/"*)
    want=$(printf '%s' "$*" | sed 's|.*refs/tags/||; s|\\^{{commit}}||')
    if [ "$want" = {TAG} ] || [ "$want" = "$(cat "{root}/made-tag" 2>/dev/null)" ]; then echo {SHA}; else exit 1; fi ;;
  *rev-parse*) echo {SHA} ;;
  *rev-list*) {f'echo {BASE}' if base_flows else ':'} ;;
  *"diff --name-only"*) printf '%s\n' {changed!r} ;;
  *show*deploy-run.sh) cat "{repo}/scripts/production/deploy-run.sh" ;;
  *" archive "*external-config-collect*) {f'tar -c -C "{repo}/ext" scripts' if external else 'exit 128'} ;;
  *" archive "*) {f'tar -c -C "{repo}" scripts/impact scripts/check-product-map.py scripts/production/check-tenant-config.py scripts/production/check-liff-endpoints.py' if with_map else 'exit 128'} ;;
esac
''')
            stub(bin_dir / 'gh', f'''echo "gh $*" >> "{trace}"
case "$1 $2" in
  "api repos/{{owner}}/{{repo}}/commits/{SHA}/status") {f'echo "{flows}"' if flows else ':'} ;;
  "api repos/{{owner}}/{{repo}}/commits/{BASE}/status") {f"echo '{base_flows}'" if base_flows else ':'} ;;
  "workflow run") ;;
  "run list")
    case "$*" in
      *"length"*) echo 0 ;;
      *build-production*) echo "101 {SHA}" ;;
      *stage-production*"--json headSha --jq"*) echo {'b' * 40} ;;  # 部署脚本查上次放上生产的提交（存量提醒用）
      *stage-production*) echo "202 {SHA}" ;;
    esac ;;
  "run view")
    case "$*" in
      *"Verify the candidate"*) echo {candidate} ;;
      *"101 "*) echo "completed|{build}|" ;;
      *"202 "*) echo "completed|{stage}|" ;;
    esac ;;
  "run download") mkdir -p "${{@: -1}}"; echo "ghcr.io/skin306152-star/pearnly/frappe@{DIGEST}" > "${{@: -1}}/image-digest.txt" ;;
esac
''')
            stub(bin_dir / 'fake-ssh', f'''printf 'ssh %s\\n' "$(printf '%s' "$*" | tr '\\n' ' ')" >> "{trace}"
case "$*" in
  *"--map-b64"*) cat > "{root}/config-script"; echo "  tenants read: erp 2"; {('case "$*" in *external-config-collect*) echo \'__EXT {"version": 1, "sites": {}}\' ;; esac;' if external_facts else '')} {('echo "__MANUAL ' + manual + '";') if manual else ''} {('echo ' + repr(json.dumps({"erp": [{"site": "mrerp.pearnly.com", "keys": {"pearnly_line_liff_id": "1-a"}}, {"site": "gize.pearnly.com", "keys": {"pearnly_line_liff_id": None}}], "dms": {"error": "x"}})) + ' >&2;') if dump else ''} {'echo "  BLOCK erp mrerp.pearnly.com pearnly_x"; exit 1' if config_rc else 'echo "  tenant config check passed"'} ;;
  *"nohup bash"*) cat > /dev/null; {'echo "BUSY abc"' if busy_server else 'echo "STARTED 4242"'} ;;
  *"tail -n"*)
    n=$(cat "{root}/attach-count" 2>/dev/null || echo 0); echo $((n + 1)) > "{root}/attach-count"
    if [ "$n" -lt {attach_failures} ]; then echo "== start"; exit 255; fi
    echo "== DEPLOY DONE"; echo "__STATUS {remote_status}" ;;
esac
''')
            env = dict(os.environ, PATH=f'{bin_dir}{os.pathsep}{os.environ["PATH"]}',
                       PEARNLY_DEPLOY_SSH=str(bin_dir / 'fake-ssh'), PEARNLY_DEPLOY_POLL_SECONDS='0',
                       PEARNLY_DEPLOY_RECONNECT_SECONDS='0')
            result = subprocess.run(['bash', str(repo / 'scripts/production/deploy.sh'), *args], env=env, text=True,
                                    capture_output=True, timeout=60)
            calls = trace.read_text().splitlines() if trace.exists() else []
            return result, calls

    @staticmethod
    def kinds(calls):
        out = []
        for c in calls:
            if c.startswith('gh workflow run'):
                out.append('dispatch ' + c.split()[3])
            elif c.startswith('ssh') and 'nohup bash' in c:
                out.append('ssh start')
            elif c.startswith('ssh') and '--map-b64' in c:
                out.append('ssh config')
            elif c.startswith('ssh'):
                out.append('ssh attach')
        return out

    def test_full_run_chains_every_step_once(self):
        result, calls = self.run_driver()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.kinds(calls), ['dispatch build-production.yml', 'dispatch stage-production.yml',
                                             'ssh start', 'ssh attach'])
        stage = next(c for c in calls if 'stage-production.yml' in c and 'workflow run' in c)
        self.assertIn(f'digest={DIGEST}', stage)
        self.assertIn('部署完成', result.stdout)

    def test_flow_cases_gate_the_deploy(self):
        cases = {
            'no result': (dict(flows=''), '找不到全套流程结果'),
            'failed': (dict(flows='failure all flows; 1 flows; datasets new,old; 3/4 runs passed; 800s'), '流程用例没通过'),
            'only affected flows': (dict(flows='success base 0123456789ab; 1 flows; datasets new,old; 4/4 runs passed; 800s'),
                                    '只跑过受影响的流程'),
            'product change since the last full suite': (
                dict(flows='', base_flows='["success", "all flows; 70 flows; 70/70 runs passed"]',
                     changed='apps/pearnly_dms/pearnly_dms/x.py'), '改到了产品路径'),
        }
        for name, (kwargs, message) in cases.items():
            with self.subTest(case=name):
                result, calls = self.run_driver(**kwargs)
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertEqual(self.kinds(calls), [])
                self.assertIn(message, result.stderr)
                self.assertIn('生产没动', result.stderr)
                self.assertIn('scripts/flows/run.py --all --post-status', result.stderr)
        result, calls = self.run_driver()
        self.assertIn('流程用例：all flows', result.stdout)
        self.assertIn('本批改到的存量模块', result.stdout)  # 存量提醒显示、不挡部署
        self.assertEqual(result.returncode, 0, result.stderr)
        result, calls = self.run_driver('--dry-run', flows='')
        self.assertEqual(result.returncode, 1)
        self.assertIn('找不到全套流程结果', result.stderr)
        # 沿用：本提交没有全套，首父链上的全套之后只改了测试 → 照常部署，打印沿用哪个提交和文件
        result, calls = self.run_driver(flows='', base_flows='["success", "all flows; 70 flows; 70/70 runs passed"]',
                                        changed='scripts/sample-verify-dms-cards.py')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f'沿用 {BASE[:8]} 的全套，之后只改了：scripts/sample-verify-dms-cards.py', result.stdout)
        self.assertEqual(self.kinds(calls)[0], 'dispatch build-production.yml')

    def test_failed_build_stops_before_production(self):
        result, calls = self.run_driver(build='failure')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.kinds(calls), ['dispatch build-production.yml'])
        self.assertIn('生产没动', result.stderr)

    def test_failed_candidate_checks_stop_before_stage(self):
        result, calls = self.run_driver(candidate='failure')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.kinds(calls), ['dispatch build-production.yml'])

    def test_failed_stage_stops_before_ssh(self):
        result, calls = self.run_driver(stage='failure')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.kinds(calls), ['dispatch build-production.yml', 'dispatch stage-production.yml'])
        self.assertIn(f'--digest {DIGEST}', result.stderr)

    def test_deploy_tags_main_and_builds_the_tag(self):
        result, calls = self.run_driver()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        made = [c for c in calls if c.startswith('git') and ' tag -a ' in c]
        self.assertEqual(len(made), 1)
        tag = made[0].split(' tag -a ')[1].split()[0]
        self.assertRegex(tag, r'^release-\d{8}-\d{4}$')
        self.assertIn(f'{SHA} -m', made[0])  # 标记打在远端 main 当前提交
        self.assertTrue(any(c.startswith('git') and f'push -q origin refs/tags/{tag}' in c for c in calls))
        build = next(c for c in calls if 'workflow run build-production.yml' in c)
        stage = next(c for c in calls if 'workflow run stage-production.yml' in c)
        self.assertIn(f'--ref {tag}', build)
        self.assertIn(f'--ref {tag}', stage)
        self.assertTrue(any('run list' in c and f'--branch {tag}' in c for c in calls))
        self.assertFalse(any('run list' in c and '--branch main' in c for c in calls))
        self.assertIn(f'发布标记 {tag}', result.stdout)

    def test_existing_tag_is_deployed_without_a_new_one(self):
        result, calls = self.run_driver('--tag', TAG)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(any(' tag -a ' in c or 'refs/tags/' in c and ' push ' in c for c in calls))
        self.assertIn(f'--ref {TAG}', next(c for c in calls if 'workflow run build-production.yml' in c))

    def test_tag_off_main_or_unknown_stops_before_anything(self):
        result, calls = self.run_driver('--tag', TAG, on_main=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn('不在 main 上', result.stderr)
        self.assertEqual(self.kinds(calls), [])
        result, calls = self.run_driver('--tag', 'release-20200101-0000')
        self.assertEqual(result.returncode, 1)
        self.assertIn('没有发布标记', result.stderr)
        self.assertEqual(self.kinds(calls), [])

    def test_bad_arguments_are_refused(self):
        for args in (('--tag', 'v1.0'), ('--digest', DIGEST)):
            with self.subTest(args=args):
                result, calls = self.run_driver(*args)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(calls, [])

    def test_tenant_config_is_compared_once_before_the_build_and_in_the_dry_run(self):
        result, calls = self.run_driver(with_map=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.kinds(calls), ['ssh config', 'dispatch build-production.yml', 'dispatch stage-production.yml',
                                             'ssh start', 'ssh attach'])
        self.assertIn('租户配置对比通过', result.stdout)
        result, calls = self.run_driver('--dry-run', with_map=True)   # 假 gh 给不出放镜像日志，演练停在取 digest，配置对比已跑过
        self.assertEqual(self.kinds(calls), ['ssh config'])
        self.assertIn('│   tenant config check passed', result.stdout)
        for args in ((), ('--dry-run',)):
            with self.subTest(args=args):
                result, calls = self.run_driver(*args, with_map=True, config_rc=1)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(self.kinds(calls), ['ssh config'])
                self.assertIn('BLOCK erp mrerp.pearnly.com', result.stdout)
                self.assertIn('正式租户的配置与测试租户对不上', result.stderr)
                self.assertIn('没有绕过开关', result.stderr)
        # 提交里还没有产品地图（更早的标记）：跳过，不连生产
        result, calls = self.run_driver('--dry-run')
        self.assertIn('还没有产品地图，跳过', result.stdout)
        self.assertEqual(self.kinds(calls), [])

    def test_liff_endpoints_are_checked_before_the_build_from_the_dumped_site_config(self):
        result, calls = self.run_driver(with_map=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('LIFF 落地地址通过', result.stdout)
        self.assertEqual(self.kinds(calls)[:2], ['ssh config', 'dispatch build-production.yml'])
        result, calls = self.run_driver(with_map=True, liff_rc=1)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.kinds(calls), ['ssh config'])
        self.assertIn('PROBLEM mrerp.pearnly.com: LIFF endpoint is /stocktake', result.stdout)
        self.assertIn('LIFF 落地地址或从 LINE 进网页的入口不对', result.stderr)
        self.assertIn('没有绕过开关', result.stderr)
        result, calls = self.run_driver('--dry-run', with_map=True, dump=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn('读不到生产机回传的站点配置', result.stderr)

    def test_external_settings_ride_the_same_ssh_and_block_before_the_build(self):
        result, calls = self.run_driver(with_map=True, external=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.kinds(calls)[:2], ['ssh config', 'dispatch build-production.yml'])   # 不多一次 SSH
        config = next(c for c in calls if c.startswith('ssh') and '--map-b64' in c)
        self.assertIn('external-config-collect', config)
        self.assertIn('站外设置对比通过', result.stdout)
        self.assertNotIn('__EXT', result.stdout)
        for args in ((), ('--dry-run',)):
            with self.subTest(args=args):
                result, calls = self.run_driver(*args, with_map=True, external=True, external_rc=1)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(self.kinds(calls), ['ssh config'])
                self.assertIn('BLOCK [ext:fatcc.com] erp mrerp.pearnly.com', result.stdout)
                self.assertIn('站外设置不对', result.stderr)
                self.assertIn('没有绕过开关', result.stderr)
        result, calls = self.run_driver('--dry-run', with_map=True, external=True, external_facts=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn('读不到生产机回传的站外设置', result.stderr)
        # 更早的标记没有这几个脚本：租户配置对比照跑，站外设置对比跳过
        result, calls = self.run_driver(with_map=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('站外设置对比：提交', result.stdout)
        self.assertNotIn('external-config-collect', next(c for c in calls if '--map-b64' in c))

    def test_containers_restarted_after_the_last_readback_are_listed_not_blocking(self):
        result, calls = self.run_driver(with_map=True, manual='pearnly-production-erp-web-1 2026-11-04T10:00:00Z')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('上次回读通过之后被重启 / 重建过的生产容器', result.stdout)
        self.assertIn('pearnly-production-erp-web-1 2026-11-04T10:00:00Z', result.stdout)
        self.assertNotIn('__MANUAL', result.stdout)
        result, calls = self.run_driver(with_map=True, manual='unknown')
        self.assertIn('还没有回读通过的时间记录', result.stdout)
        config = next(c for c in calls if '--map-b64' in c)
        self.assertIn('last-readback-ok', config)

    def test_failed_config_on_the_server_leaves_services_alone(self):
        result, calls = self.run_driver('--tag', TAG, '--digest', DIGEST, '--skip-stage', remote_status='failed config')
        self.assertEqual(result.returncode, 1)
        self.assertIn('激活前的租户配置对比没过', result.stderr)
        self.assertIn('生产服务没动', result.stderr)

    def test_dry_run_makes_no_tag(self):
        result, calls = self.run_driver('--dry-run')
        self.assertFalse(any(' tag -a ' in c for c in calls))
        self.assertFalse(any(' push ' in c for c in calls if c.startswith('git')))

    def test_digest_and_skip_stage_go_straight_to_the_server(self):
        result, calls = self.run_driver('--tag', TAG, '--digest', DIGEST, '--skip-stage')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.kinds(calls), ['ssh start', 'ssh attach'])

    def test_busy_server_is_left_alone(self):
        result, calls = self.run_driver('--tag', TAG, '--digest', DIGEST, '--skip-stage', busy_server=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.kinds(calls), ['ssh start'])
        self.assertIn('已有一次部署在跑', result.stderr)

    def test_product_failure_is_reported_without_rollback(self):
        result, calls = self.run_driver('--tag', TAG, '--digest', DIGEST, '--skip-stage', remote_status='failed cowork')
        self.assertEqual(result.returncode, 1)
        self.assertIn('激活 cowork 失败', result.stderr)
        self.assertIn('不自动回滚', result.stderr)
        self.assertFalse([c for c in calls if 'activate' in c and 'ssh' in c and 'rollback' in c])

    def test_lost_connection_reattaches_without_repeating_lines(self):
        result, calls = self.run_driver('--tag', TAG, '--digest', DIGEST, '--skip-stage', attach_failures=2)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.kinds(calls), ['ssh start', 'ssh attach', 'ssh attach', 'ssh attach'])
        attaches = [c for c in calls if c.startswith('ssh') and 'tail -n' in c]
        self.assertIn('tail -n +1 ', attaches[0])
        self.assertIn('tail -n +2 ', attaches[1])
        self.assertIn('tail -n +3 ', attaches[2])

    def test_interrupted_server_run_stops_with_guidance(self):
        result, calls = self.run_driver('--tag', TAG, '--digest', DIGEST, '--skip-stage', remote_status='running dms')
        self.assertEqual(result.returncode, 1)
        self.assertIn('部署进程已经不在', result.stderr)
        self.assertEqual(self.kinds(calls), ['ssh start', 'ssh attach'])

    def test_attach_only_reads(self):
        result, calls = self.run_driver('--attach')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.kinds(calls), ['ssh attach'])


if __name__ == '__main__':
    unittest.main()
