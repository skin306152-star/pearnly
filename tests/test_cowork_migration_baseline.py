"""Offline source baseline tests: no cloud, business IO, credentials, or user data."""
import copy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout, redirect_stderr

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/cowork-migration-baseline.py"
spec = importlib.util.spec_from_file_location("cowork_migration_baseline", SCRIPT)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)
SOURCE = "a" * 40
IMAGE = "region-docker.pkg.dev/project/repo/app@sha256:" + "1" * 64
REPO = "example/legacy"
URL = "https://github.com/example/legacy/actions/runs/123"


def fixture():
    excerpt = (f"- 完整 SHA：`{SOURCE}`。\n- 镜像：`{IMAGE}`。\n"
               "- Web `web-live`、Worker `worker-live`，Ready、各100%流量，同一不可变digest。\n"
               f"- [Manual CD 123]({URL}) success")
    components = {}
    for name in ("web", "worker"):
        service = {"metadata": {"name": name, "namespace": "12345", "generation": "7"},
                   "status": {"observedGeneration": 7, "conditions": [{"type": "Ready", "status": "True"}],
                              "traffic": [{"revisionName": name + "-live", "percent": 100}]}}
        revision = {"metadata": {"name": name + "-live", "namespace": "12345",
                                 "labels": {"serving.knative.dev/service": name}},
                    "status": {"conditions": [{"type": "Ready", "status": "True"}], "imageDigest": IMAGE},
                    "spec": {"containers": [{"image": IMAGE, "env": [
                        {"name": "NONSECRET_FLAG"},
                        {"name": "CREDENTIALS", "valueFrom": {"secretKeyRef": {"name": "secret-name", "key": "2"}}}],
                        "volumeMounts": [{"name": "runtime", "mountPath": "/secrets"}]}],
                        "volumes": [{"name": "runtime", "secret": {"secretName": name + "-env", "items": [
                            {"key": "4", "path": "runtime.env"}]}}]}}
        components[name] = {"service": service, "revision": revision, "configuration": gate.configuration(revision)}
    return {"schema": gate.SCHEMA, "captured_at": "2026-09-20T01:00:00+00:00", "project": "project", "region": "region",
            "required_services": ["web", "worker"],
            "source": {"sha": SOURCE, "tree": "b" * 40, "repository": REPO, "access": "git-show-fixed-sha"},
            "run": {"id": 123, "head_sha": SOURCE, "status": "completed", "conclusion": "success", "html_url": URL, "repository": REPO},
            "ledger": {"commit": "c" * 40, "path": "docs/deployment/MIGRATION_STATUS.md", "excerpt": excerpt,
                       "excerpt_sha256": gate.digest(excerpt)}, "components": components}


class BaselineTests(unittest.TestCase):
    def setUp(self):
        self.body = fixture()

    def test_complete_mapping_accepts_without_short_sha_in_revision(self):
        self.assertIs(gate.validate_body(self.body), self.body)
        self.assertNotIn(SOURCE[:12], "web-live")

    def test_missing_fields_fail_closed(self):
        for key in ("captured_at", "project", "source", "ledger", "run", "components", "required_services"):
            body = copy.deepcopy(self.body)
            del body[key]
            with self.subTest(key=key), self.assertRaises(gate.BaselineError):
                gate.validate_body(body)
        del self.body["components"]["worker"]
        with self.assertRaisesRegex(gate.BaselineError, "component evidence"):
            gate.validate_body(self.body)

    def test_short_sha_and_failed_run_are_not_source_proof(self):
        for key, value in [("head_sha", SOURCE[:12]), ("conclusion", "failure"), ("status", "in_progress"),
                           ("id", 456), ("repository", "other/repo")]:
            body = copy.deepcopy(self.body)
            body["run"][key] = value
            with self.subTest(key=key), self.assertRaises(gate.BaselineError):
                gate.validate_body(body)

    def test_run_and_ledger_cannot_prove_a_different_digest(self):
        self.body["components"]["worker"]["revision"]["status"]["imageDigest"] = IMAGE[:-1] + "2"
        with self.assertRaisesRegex(gate.BaselineError, "digest/source"):
            gate.validate_body(self.body)

    def test_split_routing_is_rejected(self):
        status = self.body["components"]["web"]["service"]["status"]
        status["traffic"] = [{"revisionName": "web-live", "percent": 90}, {"revisionName": "web-other", "percent": 10}]
        with self.assertRaisesRegex(gate.BaselineError, "split"):
            gate.validate_body(self.body)

    def test_zero_percent_other_tagged_route_is_unresolved(self):
        self.body["components"]["web"]["service"]["status"]["traffic"].append({"revisionName": "web-other", "tag": "candidate"})
        with self.assertRaisesRegex(gate.BaselineError, "tagged"):
            gate.validate_body(self.body)

    def test_ready_candidate_does_not_replace_actual_traffic(self):
        self.body["components"]["web"]["revision"]["metadata"]["name"] = "web-candidate"
        with self.assertRaisesRegex(gate.BaselineError, "traffic/revision"):
            gate.validate_body(self.body)

    def test_unconverged_and_wrong_project_revision_rejected(self):
        for mutate in [lambda b: b["components"]["web"]["service"]["status"].update(observedGeneration=6),
                       lambda b: b["components"]["web"]["revision"]["metadata"].update(namespace="other")]:
            body = copy.deepcopy(self.body)
            mutate(body)
            with self.assertRaises(gate.BaselineError):
                gate.validate_body(body)

    def test_plain_env_values_never_enter_saved_evidence(self):
        self.body["components"]["web"]["revision"]["spec"]["containers"][0]["env"][0]["value"] = "sensitive-test-only"
        with self.assertRaisesRegex(gate.BaselineError, "plain environment"):
            gate.envelope(self.body)

    def test_provider_projection_discards_values_args_and_unrelated_metadata(self):
        raw = copy.deepcopy(self.body["components"]["web"]["revision"])
        raw["spec"]["containers"][0]["env"][0]["value"] = "sensitive-test-only"
        raw["spec"]["containers"][0]["args"] = ["--password=sensitive-test-only"]
        raw["metadata"]["annotations"] = {"unrelated": "sensitive-test-only"}
        projected = gate.project_revision(raw)
        self.assertNotIn("sensitive-test-only", json.dumps(projected))
        self.assertEqual(gate.configuration(projected), self.body["components"]["web"]["configuration"])

    def test_mutable_secret_references_cannot_be_confirmed(self):
        for kind in ["env", "volume"]:
            body = copy.deepcopy(self.body)
            revision = body["components"]["web"]["revision"]
            if kind == "env":
                revision["spec"]["containers"][0]["env"][1]["valueFrom"]["secretKeyRef"]["key"] = "latest"
            else:
                revision["spec"]["volumes"][0]["secret"]["items"][0]["key"] = "latest"
            with self.subTest(kind=kind), self.assertRaisesRegex(gate.BaselineError, "mutable"):
                gate.validate_body(body)

    def test_evidence_modified_and_rehashed_evidence_external_anchor(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "first.json"
            original = gate.save_new(path, self.body)
            data = json.loads(path.read_text())
            data["evidence"]["captured_at"] = "2026-09-20T02:00:00+00:00"
            path.chmod(0o600)
            path.write_text(json.dumps(data))
            with self.assertRaisesRegex(gate.BaselineError, "checksum mismatch"):
                gate.load(path)
            data["sha256"] = gate.digest(data["evidence"])
            path.write_text(json.dumps(data))
            with self.assertRaisesRegex(gate.BaselineError, "independently recorded"):
                gate.load(path, original)

    def test_evidence_is_write_once(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "first.json"
            gate.save_new(path, self.body)
            with self.assertRaises(FileExistsError):
                gate.save_new(path, self.body)
            self.assertEqual(path.stat().st_mode & 0o777, 0o444)

    def test_same_file_is_not_a_final_read(self):
        with self.assertRaisesRegex(gate.BaselineError, "later independent"):
            gate.compare(self.body, self.body)

    def test_unchanged_independent_read_accepts(self):
        later = copy.deepcopy(self.body)
        later["captured_at"] = "2026-09-20T02:00:00+00:00"
        self.assertTrue(gate.compare(self.body, later))

    def test_valid_but_new_source_identity_requires_rebaseline(self):
        later = copy.deepcopy(self.body)
        later["captured_at"] = "2026-09-20T02:00:00+00:00"
        newer = "d" * 40
        later["source"]["sha"] = later["run"]["head_sha"] = newer
        later["ledger"]["excerpt"] = later["ledger"]["excerpt"].replace(SOURCE, newer)
        later["ledger"]["excerpt_sha256"] = gate.digest(later["ledger"]["excerpt"])
        with self.assertRaisesRegex(gate.BaselineError, "source drift"):
            gate.compare(self.body, later)

    def test_new_digest_with_consistent_mapping_still_fails_final_compare(self):
        later = json.loads(json.dumps(self.body).replace(IMAGE, IMAGE[:-1] + "2"))
        later["captured_at"] = "2026-09-20T02:00:00+00:00"
        later["ledger"]["excerpt_sha256"] = gate.digest(later["ledger"]["excerpt"])
        with self.assertRaisesRegex(gate.BaselineError, "image drift"):
            gate.compare(self.body, later)

    def test_new_revision_with_consistent_mapping_still_fails_final_compare(self):
        later = json.loads(json.dumps(self.body).replace("web-live", "web-new"))
        later["captured_at"] = "2026-09-20T02:00:00+00:00"
        later["ledger"]["excerpt_sha256"] = gate.digest(later["ledger"]["excerpt"])
        with self.assertRaisesRegex(gate.BaselineError, "revision drift"):
            gate.compare(self.body, later)

    def test_config_projection_changes_fail_even_if_identity_claim_unchanged(self):
        later = copy.deepcopy(self.body)
        later["captured_at"] = "2026-09-20T02:00:00+00:00"
        component = later["components"]["web"]
        component["revision"]["spec"]["volumes"][0]["secret"]["items"][0]["key"] = "5"
        component["configuration"] = gate.configuration(component["revision"])
        with self.assertRaisesRegex(gate.BaselineError, "configuration drift"):
            gate.compare(self.body, later)

    def test_ledger_truncates_logs_and_historic_business_data(self):
        raw = "## 正在服务的发布身份\n\n" + self.body["ledger"]["excerpt"] + " BUSINESS_DATA\n- USER_DATA\n## Next\nOTHER"
        mapping = gate.ledger_identity(raw)
        self.assertNotIn("BUSINESS_DATA", mapping["excerpt"])
        self.assertNotIn("USER_DATA", mapping["excerpt"])

    def test_default_verify_never_executes_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "first.json"
            gate.save_new(path, self.body)
            with patch.object(gate, "command", side_effect=AssertionError("unexpected IO")), redirect_stdout(io.StringIO()):
                self.assertEqual(gate.main([str(path)]), 0)

    def test_local_provenance_verifies_tree_and_fixed_ledger(self):
        raw = "## 正在服务的发布身份\n\n" + self.body["ledger"]["excerpt"]
        with patch.object(gate, "command", side_effect=["b" * 40, raw]) as run:
            gate.verify_source(self.body, "/read-only-source")
            self.assertEqual(run.call_args_list[1].args[0][3], "show")
        with patch.object(gate, "command", return_value="0" * 40):
            with self.assertRaisesRegex(gate.BaselineError, "source tree"):
                gate.verify_source(self.body, "/read-only-source")

    def test_collect_only_uses_read_commands_and_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capture.json"
            arguments = ["collect-readonly", "--project", "project", "--region", "region", "--services", "web", "worker",
                         "--source-repo", "/legacy", "--github-repo", REPO, "--run-id", "123", "--output", str(path)]
            calls = []

            def execute(args):
                calls.append(args)
                if args[0] == "git":
                    if args[3] == "show":
                        return "## 正在服务的发布身份\n\n" + self.body["ledger"]["excerpt"]
                    return ("b" if args[-1].endswith("^{tree}") else "c") * 40
                if args[0] == "gh":
                    return json.dumps(self.body["run"])
                self.assertEqual(args[:2], ["gcloud", "run"])
                self.assertEqual(args[3], "describe")
                name = args[4].replace("-live", "")
                return json.dumps(self.body["components"][name]["service" if args[2] == "services" else "revision"])

            with patch.object(gate, "command", side_effect=execute), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(gate.main(arguments), 0)
                self.assertEqual(gate.main(arguments), 1)
            self.assertEqual(len(calls), 10)
            gate.load(path)

    def test_changed_during_collection_leaves_no_accepted_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capture.json"
            arguments = ["collect-readonly", "--project", "project", "--region", "region", "--services", "web", "worker",
                         "--source-repo", "/legacy", "--github-repo", REPO, "--run-id", "123", "--output", str(path)]
            changed = copy.deepcopy(self.body["components"]["web"]["service"])
            changed["status"]["traffic"][0]["revisionName"] = "web-new"
            values = ["c" * 40, "## 正在服务的发布身份\n\n" + self.body["ledger"]["excerpt"], "b" * 40,
                      json.dumps(self.body["run"])]
            for name in ["web", "worker"]:
                values.extend(json.dumps(self.body["components"][name][kind]) for kind in ["service", "revision"])
            values.append(json.dumps(changed))
            with patch.object(gate, "command", side_effect=values), redirect_stderr(io.StringIO()):
                self.assertEqual(gate.main(arguments), 1)
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
