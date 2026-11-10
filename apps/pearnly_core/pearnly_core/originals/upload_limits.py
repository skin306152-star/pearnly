"""上传大小的落地（多产品共用）：把 bench 的请求体上限设成 `rules.UPLOAD_REQUEST_MAX_BYTES`，超限时照实说多大。

Frappe 除它自己的 `upload_file` 外，**整个请求体**按 bench 配置 `max_file_size` 收，没配就是 25MB（`frappe/app.py`
的 `init_request`；它在任何钩子之前就解析表单，接口里放宽不了）。11-10 压测：CC 说一份最多 30MB，27.7MB 的 50 页扫描 PDF
在约 25MB 处被回 413，页面只说「出错了」。线上三个产品激活时跑本模块（`scripts/production/activate-product.sh`），
实验栈（`scripts/regression/lab_init.py`）与测试机（`infrastructure/testenv/finish-start.py`）用 `bench_values` 写同一个值。

    env/bin/python -m pearnly_core.originals.upload_limits [--path <common_site_config.json>]
"""
import argparse
import json
from pathlib import Path

from pearnly_core.originals.rules import UPLOAD_MAX_BYTES, UPLOAD_REQUEST_MAX_BYTES

COMMON = "/home/frappe/frappe-bench/sites/common_site_config.json"


def bench_values(values: dict) -> dict:
    """公共站点配置加上请求体上限（新的一份，不改传进来的）。"""
    return {**values, "max_file_size": UPLOAD_REQUEST_MAX_BYTES}


def install(path=None) -> None:
    target = Path(path or COMMON)
    data = bench_values(json.loads(target.read_text()))
    temporary = target.with_suffix(".upload.tmp")
    temporary.write_text(json.dumps(data, indent=2))
    temporary.chmod(target.stat().st_mode & 0o777)
    temporary.replace(target)


def too_large() -> str:
    """超限时给人看的那句（四语词条 `upload.tooLarge`，带上限的 MB 数）。"""
    from frappe import _

    return _("upload.tooLarge").format(UPLOAD_MAX_BYTES // (1024 * 1024))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path")
    install(parser.parse_args().path)
