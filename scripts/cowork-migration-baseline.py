#!/usr/bin/env python3
"""Offline Cowork source identity gate; collect-readonly explicitly reads cloud metadata.

Never deploys, reads Secret Manager payloads, writes to the source repository, or
contacts business APIs. Evidence hashes detect changes, not malicious forgery.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

SCHEMA = "pearnly-cowork-migration-baseline-v1"
SHA = re.compile(r"[0-9a-f]{40}\Z")
DIGEST = re.compile(r"[^\s@]+@sha256:[0-9a-f]{64}\Z")
SERVICE_FORMAT = "json(metadata.name,metadata.namespace,metadata.generation,status.observedGeneration,status.conditions,status.traffic,status.url)"
REVISION_FORMAT = "json"
RUN_FILTER = "{id,head_sha,status,conclusion,html_url,repository: .repository.full_name}"


class BaselineError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise BaselineError(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def timestamp(value):
    require(isinstance(value, str), "captured_at must include a timezone")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BaselineError("invalid captured_at") from exc
    require(result.tzinfo is not None, "captured_at must include a timezone")
    return result


def command(args):
    # No shell and no stderr forwarding: provider errors may contain sensitive context.
    try:
        return subprocess.run(args, check=True, capture_output=True, text=True, timeout=90).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise BaselineError(f"read-only {args[0]} command failed; no evidence was accepted") from exc


def read_json_command(args):
    try:
        return json.loads(command(args))
    except json.JSONDecodeError as exc:
        raise BaselineError(f"invalid JSON from {args[0]}") from exc


def ledger_identity(contents):
    """Select only deployment identity, never historic business/user-data paragraphs."""
    match = re.search(r"^## 正在服务的发布身份\s*\n(.*?)(?=^## |\Z)", contents, re.M | re.S)
    require(match, "deployment ledger lacks the current serving identity section")
    lines = [line for line in match.group(1).splitlines() if line.strip()][:4]
    require(len(lines) == 4, "deployment ledger identity is incomplete")
    source = re.fullmatch(r"- 完整 SHA：`([0-9a-f]{40})`。", lines[0])
    image = re.fullmatch(r"- 镜像：`([^`]+)`。", lines[1])
    revisions = re.match(r"- Web `([^`]+)`、Worker `([^`]+)`", lines[2])
    run = re.match(r"- \[Manual CD (\d+)\]\((https://github\.com/[^)]+/actions/runs/\d+)\) success", lines[3])
    require(source and image and revisions and run, "unsupported or unproven ledger identity; review it explicitly")
    require(DIGEST.fullmatch(image.group(1)), "ledger image is not immutable")
    # Keep only the safe mapping fragments; the remainder of line 4 can contain logs.
    excerpt = "\n".join([lines[0], lines[1], lines[2], run.group(0)])
    return {"source_sha": source.group(1), "image_digest": image.group(1),
            "revisions": list(revisions.groups()), "run_id": int(run.group(1)),
            "run_url": run.group(2), "excerpt": excerpt}


def normalized_ledger(excerpt):
    return ledger_identity("## 正在服务的发布身份\n\n" + excerpt)


def ready(document, label):
    conditions = document["status"]["conditions"]
    require(isinstance(conditions, list) and any(
        row.get("type") == "Ready" and row.get("status") in (True, "True") for row in conditions
    ), f"{label} is not Ready")


def active_revision(service):
    ready(service, "service")
    metadata, status = service["metadata"], service["status"]
    require(int(status["observedGeneration"]) == int(metadata["generation"]), "service generation has not converged")
    traffic = status["traffic"]
    require(isinstance(traffic, list) and traffic, "actual service traffic is missing")
    active = []
    for target in traffic:
        percent = target.get("percent", 0)
        require(type(percent) is int and 0 <= percent <= 100, "invalid traffic percentage")
        if percent:
            active.append(target)
    require(len(active) == 1 and active[0]["percent"] == 100, "split or unknown traffic; migration baseline unconfirmed")
    name = active[0]["revisionName"]
    require(isinstance(name, str) and name, "traffic lacks an explicit revision")
    require(all(not t.get("tag") or t.get("revisionName") == name for t in traffic),
            "another revision has a tagged route; its production usage needs review")
    return name


def project_revision(raw):
    """Drop values immediately: gcloud's nested-array projection loses fields.

    The describe response can contain plain configuration values in memory. They
    are neither logged nor saved; Secret Manager payloads are never requested.
    """
    meta, status, spec = raw["metadata"], raw["status"], raw["spec"]
    projected = {"metadata": {key: meta[key] for key in ("name", "namespace")},
                 "status": {key: status[key] for key in ("conditions", "imageDigest")},
                 "spec": {"containers": [], "volumes": []}}
    projected["metadata"]["labels"] = {"serving.knative.dev/service": meta["labels"]["serving.knative.dev/service"]}
    aliases = meta.get("annotations", {}).get("run.googleapis.com/secrets")
    if aliases:
        projected["metadata"]["secret_aliases"] = aliases
    for container in spec["containers"]:
        envs = []
        for env in container.get("env", []):
            item = {"name": env["name"]}
            if "valueFrom" in env:
                ref = env["valueFrom"]["secretKeyRef"]
                item["valueFrom"] = {"secretKeyRef": {"name": ref["name"], "key": ref["key"]}}
            envs.append(item)
        projected["spec"]["containers"].append({"image": container["image"], "env": envs,
            "volumeMounts": [{"name": m["name"], "mountPath": m["mountPath"]} for m in container.get("volumeMounts", [])]})
    for volume in spec.get("volumes", []):
        item = {"name": volume["name"]}
        if "secret" in volume:
            secret = volume["secret"]
            item["secret"] = {"secretName": secret["secretName"], "items": [
                {"key": i["key"], "path": i["path"]} for i in secret.get("items", [])]}
        elif "csi" in volume:
            csi = volume["csi"]
            item["csi"] = {"driver": csi["driver"], "readOnly": csi.get("readOnly", False),
                "volumeAttributes": {k: v for k, v in csi.get("volumeAttributes", {}).items() if k in {"bucketName", "mountOptions"}}}
        elif "emptyDir" in volume:
            item["emptyDir"] = {k: v for k, v in volume["emptyDir"].items() if k in {"medium", "sizeLimit"}}
        else:
            raise BaselineError("unsupported mounted volume; source override needs review")
        projected["spec"]["volumes"].append(item)
    return projected


def configuration(revision):
    """No plain env values are retained. Revision identity covers their immutability."""
    containers = revision["spec"]["containers"]
    require(isinstance(containers, list) and len(containers) == 1, "multiple/unknown containers need an explicit source mapping")
    container = containers[0]
    require(DIGEST.fullmatch(container["image"]), "revision container image is not digest-pinned")
    refs, names = [], []
    for env in container.get("env", []):
        require(set(env) <= {"name", "valueFrom"}, "plain environment values must not enter evidence")
        names.append(env["name"])
        if "valueFrom" in env:
            ref = env["valueFrom"]["secretKeyRef"]
            require(set(env["valueFrom"]) == {"secretKeyRef"}, "unknown environment reference")
            require(re.fullmatch(r"[1-9][0-9]*", str(ref["key"])), "mutable/unknown secret version; cannot confirm runtime configuration")
            refs.append({"kind": "environment", "target": env["name"], "name": ref["name"], "version": str(ref["key"])})
    require(len(set(names)) == len(names), "duplicate environment keys")
    for volume in revision["spec"].get("volumes", []):
        if "secret" not in volume:
            continue
        secret = volume["secret"]
        require(secret.get("items"), "secret volume has no pinned versions")
        for item in secret["items"]:
            require(re.fullmatch(r"[1-9][0-9]*", str(item["key"])), "mutable/unknown secret volume version")
            refs.append({"kind": "volume", "target": volume["name"] + "/" + item["path"],
                         "name": secret["secretName"], "version": str(item["key"])})
    return {"environment_names": sorted(names), "secret_references": sorted(refs, key=canonical),
            "mounts": container.get("volumeMounts", []), "volumes": revision["spec"].get("volumes", []),
            "secret_aliases": revision["metadata"].get("secret_aliases", "")}


def validate_body(body):
    try:
        require(body["schema"] == SCHEMA, "unknown baseline schema")
        timestamp(body["captured_at"])
        require(body["project"] and body["region"], "project/region missing")
        source, run, ledger = body["source"], body["run"], body["ledger"]
        require(SHA.fullmatch(source["sha"]) and SHA.fullmatch(source["tree"]), "source must have full commit and tree identities")
        require(source["repository"] and source["access"] == "git-show-fixed-sha", "fixed source access missing")
        require(SHA.fullmatch(ledger["commit"]), "ledger must be read from a fixed commit")
        require(ledger["path"] and digest(ledger["excerpt"]) == ledger["excerpt_sha256"], "ledger excerpt integrity mismatch")
        identity = normalized_ledger(ledger["excerpt"])
        require(identity["source_sha"] == source["sha"] == run["head_sha"], "source SHA mapping mismatch")
        require(run["status"] == "completed" and run["conclusion"] == "success", "source deployment run is not successful")
        require(type(run["id"]) is int and run["id"] == identity["run_id"], "deployment run identity mismatch")
        expected_url = f'https://github.com/{source["repository"]}/actions/runs/{run["id"]}'
        require(run["repository"] == source["repository"] and run["html_url"] == identity["run_url"] == expected_url,
                "deployment run repository/URL mismatch")
        components = body["components"]
        require(isinstance(components, dict) and set(components) == set(body["required_services"]), "required component evidence missing")
        require(len(components) == len(identity["revisions"]) == 2, "this gate requires both Web and Worker")
        seen = set()
        namespaces = set()
        for name, component in components.items():
            service, revision = component["service"], component["revision"]
            require(service["metadata"]["name"] == name, "service name mismatch")
            revision_name = active_revision(service)
            require(revision["metadata"]["name"] == revision_name, "traffic/revision evidence mismatch")
            require(revision["metadata"]["namespace"] == service["metadata"]["namespace"], "component project identity mismatch")
            require(revision["metadata"]["labels"]["serving.knative.dev/service"] == name, "revision belongs to another service")
            ready(revision, name + " revision")
            require(revision["status"]["imageDigest"] == identity["image_digest"], "runtime digest/source mapping mismatch")
            require(revision_name in identity["revisions"], "runtime revision is absent from deployment mapping")
            config = configuration(revision)
            require(revision["spec"]["containers"][0]["image"] == identity["image_digest"], "container and resolved image digests differ")
            require(component["configuration"] == config, "configuration projection mismatch")
            seen.add(revision_name)
            namespaces.add(service["metadata"]["namespace"])
        require(len(namespaces) == 1 and next(iter(namespaces)), "Web/Worker must belong to the same project")
        require(seen == set(identity["revisions"]), "Web/Worker mapping is incomplete")
        return body
    except (KeyError, TypeError, AttributeError, OverflowError) as exc:
        raise BaselineError("missing or malformed baseline evidence") from exc
    except ValueError as exc:
        if isinstance(exc, BaselineError):
            raise
        raise BaselineError("malformed baseline evidence") from exc


def envelope(body):
    validate_body(body)
    return {"evidence": body, "sha256": digest(body)}


def load(path, expected=None):
    try:
        data = json.loads(Path(path).read_text())
        require(set(data) == {"evidence", "sha256"}, "invalid evidence envelope")
        require(data["sha256"] == digest(data["evidence"]), "evidence was changed (checksum mismatch)")
        if expected:
            require(data["sha256"] == expected, "evidence differs from the independently recorded checksum")
        return validate_body(data["evidence"])
    except (OSError, json.JSONDecodeError) as exc:
        raise BaselineError("cannot read baseline JSON") from exc


def verify_source(body, repo):
    """Optional local-only provenance verification, without checkout/fetch/pull."""
    source, ledger = body["source"], body["ledger"]
    tree = command(["git", "-C", str(repo), "rev-parse", "--verify", source["sha"] + "^{tree}"]).strip()
    require(tree == source["tree"], "local source tree differs from evidence")
    raw = command(["git", "-C", str(repo), "show", ledger["commit"] + ":" + ledger["path"]])
    require(ledger_identity(raw)["excerpt"] == ledger["excerpt"], "fixed Git ledger differs from evidence")


def compare(before, after):
    validate_body(before)
    validate_body(after)
    require(timestamp(after["captured_at"]) > timestamp(before["captured_at"]), "final evidence must be a later independent read")
    require((before["project"], before["region"], before["required_services"]) ==
            (after["project"], after["region"], after["required_services"]), "baseline scopes differ")
    require(before["source"]["repository"] == after["source"]["repository"], "source repositories differ")
    require(before["source"]["sha"] == after["source"]["sha"] and before["source"]["tree"] == after["source"]["tree"],
            "source drift: compare affected code/dependencies, migrate changes, then rebaseline")
    for name in before["required_services"]:
        old, new = before["components"][name], after["components"][name]
        require(old["service"]["metadata"]["namespace"] == new["service"]["metadata"]["namespace"], f"{name} project identity drift")
        require(active_revision(old["service"]) == active_revision(new["service"]), f"{name} revision drift")
        require(old["revision"]["status"]["imageDigest"] == new["revision"]["status"]["imageDigest"], f"{name} image drift")
        require(old["configuration"] == new["configuration"], f"{name} configuration drift")
    return True


def save_new(path, body):
    data = envelope(body)
    # Exclusive create + read-only permissions: never replace earlier evidence.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    with os.fdopen(fd, "w") as stream:
        json.dump(data, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write("\n")
    return data["sha256"]


def collect(args):
    require(not Path(args.output).exists(), "output exists; use a new evidence filename")
    require(re.fullmatch(r"[\w.-]+/[\w.-]+", args.github_repo), "invalid GitHub repository")
    require(len(args.services) == 2 and len(set(args.services)) == 2, "select exactly the Web and Worker services")
    ledger_commit = command(["git", "-C", args.source_repo, "rev-parse", "--verify", args.ledger_ref + "^{commit}"]).strip()
    require(SHA.fullmatch(ledger_commit), "invalid ledger Git commit")
    raw = command(["git", "-C", args.source_repo, "show", ledger_commit + ":" + args.ledger_path])
    identity = ledger_identity(raw)
    require(identity["run_id"] == args.run_id, "run does not match the deployment ledger")
    tree = command(["git", "-C", args.source_repo, "rev-parse", "--verify", identity["source_sha"] + "^{tree}"]).strip()
    run = read_json_command(["gh", "api", f"repos/{args.github_repo}/actions/runs/{args.run_id}", "--jq", RUN_FILTER])
    components = {}
    for name in args.services:
        base = ["--project", args.project, "--region", args.region]
        service = read_json_command(["gcloud", "run", "services", "describe", name, *base, "--format=" + SERVICE_FORMAT])
        revision_name = active_revision(service)
        revision = project_revision(read_json_command(["gcloud", "run", "revisions", "describe", revision_name, *base, "--format=" + REVISION_FORMAT]))
        components[name] = {"service": service, "revision": revision, "configuration": configuration(revision)}
    # Detect a rollout during capture. The resulting evidence is still time-bound.
    for name in args.services:
        again = read_json_command(["gcloud", "run", "services", "describe", name, "--project", args.project,
                                   "--region", args.region, "--format=" + SERVICE_FORMAT])
        require(again == components[name]["service"], "service changed during collection; capture again")
    body = {"schema": SCHEMA, "captured_at": utc_now(), "project": args.project, "region": args.region,
            "required_services": sorted(args.services), "source": {"sha": identity["source_sha"], "tree": tree,
            "repository": args.github_repo, "access": "git-show-fixed-sha"}, "run": run,
            "ledger": {"commit": ledger_commit, "path": args.ledger_path, "excerpt": identity["excerpt"],
                       "excerpt_sha256": digest(identity["excerpt"])}, "components": components}
    checksum = save_new(args.output, body)
    print(f"PASS source identity collected: {args.output}\nsha256={checksum}\nsource={identity['source_sha']}")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] not in {"verify", "compare", "collect-readonly", "-h", "--help"}:
        argv.insert(0, "verify")
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    verify = sub.add_parser("verify", help="offline, default: validate existing evidence")
    verify.add_argument("evidence")
    verify.add_argument("--expected-sha256")
    verify.add_argument("--source-repo", help="also verify the fixed local Git ledger/tree, without network")
    diff = sub.add_parser("compare", help="offline: fail if first/final source identities drift")
    diff.add_argument("before")
    diff.add_argument("after")
    diff.add_argument("--before-sha256")
    diff.add_argument("--after-sha256")
    read = sub.add_parser("collect-readonly", help="explicit network reads: Cloud Run/GitHub metadata only")
    read.add_argument("--project", required=True)
    read.add_argument("--region", required=True)
    read.add_argument("--services", nargs=2, required=True)
    read.add_argument("--source-repo", required=True)
    read.add_argument("--ledger-ref", default="origin/master")
    read.add_argument("--ledger-path", default="docs/deployment/MIGRATION_STATUS.md")
    read.add_argument("--github-repo", required=True)
    read.add_argument("--run-id", type=int, required=True)
    read.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        if args.action == "collect-readonly":
            collect(args)
        elif args.action == "compare":
            before = load(args.before, args.before_sha256)
            after = load(args.after, args.after_sha256)
            compare(before, after)
            print("PASS source identities unchanged within the recorded Cloud Run scope; business parity is separate")
        else:
            body = load(args.evidence, args.expected_sha256)
            if args.source_repo:
                verify_source(body, args.source_repo)
            print(f"PASS source identity evidence: {body['source']['sha']}; business parity is separate")
        return 0
    except (KeyError, TypeError, AttributeError) as exc:
        print("FAIL baseline unconfirmed: missing or malformed provider evidence", file=sys.stderr)
        return 1
    except (BaselineError, OSError) as exc:
        print(f"FAIL baseline unconfirmed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
