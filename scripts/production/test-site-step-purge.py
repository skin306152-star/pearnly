#!/usr/bin/env python3
"""No network: deleting a tenant also deletes its files in the Cowork originals bucket, and only its own."""
import importlib.util
import io
import json
from pathlib import Path
import sys
import unittest
import urllib.error
import urllib.parse

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps/pearnly_core"))
path = Path(__file__).resolve().parents[2] / "infrastructure/production/site-step.py"
spec = importlib.util.spec_from_file_location("site_step", path)
step = importlib.util.module_from_spec(spec)
spec.loader.exec_module(step)

SITE = "acme-cowork.pearnly.com"


class FakeBucket:
    """Stands in for the Cloud Storage JSON API: paged listing by prefix, DELETE by name + generation."""

    def __init__(self, names, page_size=2, gone=()):
        self.objects = {n: str(100 + i) for i, n in enumerate(names)}
        self.page_size, self.gone, self.calls = page_size, set(gone), []

    def __call__(self, request, timeout):
        url = urllib.parse.urlsplit(request.full_url)
        self.calls.append((request.get_method(), url.path, request.get_header("Authorization")))
        if request.get_method() == "DELETE":
            name = urllib.parse.unquote(url.path.rsplit("/o/", 1)[1])
            if name in self.gone:
                raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)
            del self.objects[name]
            return io.BytesIO(b"")
        query = dict(urllib.parse.parse_qsl(url.query))
        # Like the real API the page token is a cursor (continue after this name), so deleting while paging is safe.
        names = sorted(n for n in self.objects if n.startswith(query["prefix"]) and n > query.get("pageToken", ""))
        chunk = names[:self.page_size]
        body = {"items": [{"name": n, "generation": self.objects[n]} for n in chunk]}
        if len(names) > self.page_size:
            body["nextPageToken"] = chunk[-1]
        return io.BytesIO(json.dumps(body).encode())


class PurgeSiteObjects(unittest.TestCase):
    def test_deletes_every_page_of_the_tenants_originals_and_previews_only(self):
        mine = [f"{SITE}/originals/CWS-{i}.pdf" for i in range(3)] + [f"{SITE}/previews/CWS-0/v1-abc/p1"]
        other = ["other-cowork.pearnly.com/originals/CWS-9.pdf", "acme-cowork.pearnly.com.evil/originals/x.pdf"]
        bucket = FakeBucket(mine + other)
        self.assertEqual(step.purge_site_objects("originals-sg", SITE, "tok", opener=bucket), 4)
        self.assertEqual(sorted(bucket.objects), sorted(other))
        self.assertEqual({c[2] for c in bucket.calls}, {"Bearer tok"})

    def test_an_object_already_gone_counts_as_deleted_so_the_step_can_rerun(self):
        names = [f"{SITE}/originals/a.pdf", f"{SITE}/originals/b.pdf"]
        bucket = FakeBucket(names, gone={names[0]})
        self.assertEqual(step.purge_site_objects("originals-sg", SITE, "tok", opener=bucket), 2)

    def test_refuses_any_prefix_that_could_reach_other_tenants(self):
        for site in ("", " ", "a/b.pearnly.com", "localhost", f" {SITE}"):
            with self.subTest(site=site), self.assertRaises(ValueError):
                step.purge_site_objects("originals-sg", site, "tok", opener=FakeBucket([]))

    def test_no_bucket_configured_means_nothing_to_delete(self):
        bucket = FakeBucket([f"{SITE}/originals/a.pdf"])
        self.assertEqual(step.purge_site_objects("", SITE, "tok", opener=bucket), 0)
        self.assertEqual(bucket.calls, [])


if __name__ == "__main__":
    unittest.main()
