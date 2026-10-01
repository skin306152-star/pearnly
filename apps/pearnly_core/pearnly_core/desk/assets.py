"""Content-versioned native hook assets, without changing Frappe's asset loader.

产品前端的 Desk 脚本 / 样式（``app_include_js`` / ``app_include_css``）原来一个文件一个请求：ERP 首次打开要拉 68 个、
Cowork 35 个、DMS 23 个（2026-10-01 生产实测，办公室网络单是并行下载就要 1.7–1.9 秒）。现在按 Frappe 官方做法打包：
生产镜像构建时 ``scripts/build-desk-bundles.py`` 按 hooks 里的清单生成官方入口（``*.bundle.js`` / ``*.bundle.css``，
按原顺序 import 各文件），``bench build --production`` 用 esbuild 打成带内容哈希、压缩过的文件并登记进 ``assets.json``，
页面渲染时 Frappe 按包名查到真实地址（``frappe.utils.jinja_globals.bundled_asset``），长期缓存。
（2026-10-01 实测 Cowork：12 个文件打成一个 175 KB / gzip 53 KB，原样拼接是 325 KB / 99 KB。）

``desk_bundle`` 只在打好的包确实由当前源码打出来（构建时记下的逐个源文件摘要对得上、包文件在）时才交出包名；
否则（开发、测试机挂实时源码把镜像里打好的包盖住、没构建）退回原来的逐个加载，内容版本号照旧。
"""
import json
from hashlib import sha256
from importlib.util import find_spec
from pathlib import Path

SOURCES = "dist/desk-sources.json"


def _public(app):
    return Path(find_spec(app).origin).parent / "public"


def _assets_dir():
    # <bench>/apps/frappe/frappe/__init__.py → <bench>/sites/assets（生产镜像入口把它链到镜像里打好的那份）
    return Path(find_spec("frappe").origin).parents[3] / "sites" / "assets"


def versioned_asset(app, relative):
    version = sha256((_public(app) / relative).read_bytes()).hexdigest()[:16]
    return f"/assets/{app}/{relative}?v={version}"


def digests(app, files):
    public = _public(app)
    return [sha256((public / relative).read_bytes()).hexdigest() for relative in files]


def _built(app, bundle, files):
    try:
        entry = json.loads((_public(app) / SOURCES).read_text()).get(bundle) or {}
        if entry.get("sources") != list(files) or entry.get("digests") != digests(app, files):
            return False
        path = json.loads((_assets_dir() / "assets.json").read_text()).get(bundle) or ""
    except (OSError, ValueError):
        return False
    return path.startswith("/assets/") and (_assets_dir() / path[len("/assets/"):]).is_file()


def desk_bundle(app, bundle, files):
    """``files``（``public/`` 下的相对路径，顺序即加载顺序）→ hooks 要的清单：打好的包名，或逐个带版本号的地址。"""
    files = list(files)
    if files and _built(app, bundle, files):
        return [bundle]
    return [versioned_asset(app, relative) for relative in files]
