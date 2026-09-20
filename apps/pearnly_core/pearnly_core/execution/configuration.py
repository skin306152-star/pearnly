"""Idempotent shared queue configuration for native bench deployments."""
import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit


def install(redis_url, path=None):
    parsed = urlsplit(redis_url)
    if parsed.scheme not in ('redis', 'rediss') or not parsed.hostname or parsed.path != '/6':
        raise ValueError('A dedicated shared capacity Redis database /6 is required')
    target = Path(path or '/home/frappe/frappe-bench/sites/common_site_config.json')
    data = json.loads(target.read_text())
    existing = data.get('pearnly_ocr_budget_redis')
    if existing and existing != redis_url:
        raise ValueError('Changing the shared capacity store requires draining active OCR first')
    data['pearnly_ocr_budget_redis'] = redis_url
    data.setdefault('workers', {}).setdefault('ocr', {})['timeout'] = 600
    temporary = target.with_suffix('.ocr.tmp')
    temporary.write_text(json.dumps(data, indent=2))
    temporary.chmod(target.stat().st_mode & 0o777)
    temporary.replace(target)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--redis-url', required=True)
    args = parser.parse_args()
    install(args.redis_url)
