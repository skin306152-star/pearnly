#!/usr/bin/env python3
"""生产镜像构建时生成 Frappe 官方打包入口（在 ``bench build`` 之前跑）：每个自有 App 按 hooks 里的 ``DESK_JS`` / ``DESK_CSS``
清单，写一个 ``public/js/<app>_desk.bundle.js``（按原顺序 ``import`` 各文件）和 ``public/scss/<app>_desk.bundle.scss``
（按原顺序 ``@import``，产物登记为 ``<app>_desk.bundle.css``），再把逐个源文件的摘要写进 ``public/dist/desk-sources.json``（运行时 ``desk_bundle`` 据此确认包与源码一致）。
之后由 ``bench build --app <app> --production`` 用 esbuild 打包、压缩、登记进 assets.json。

只读 hooks.py 里的字面清单（不导入 App、不需要站点），只写上面三处（都被 .gitignore 忽略，不进仓库）。
用法：python3 scripts/build-desk-bundles.py [apps 目录]，回打包了的 App 名（每行一个）。
"""
import ast
import json
import sys
from hashlib import sha256
from pathlib import Path

APPS = ("pearnly_core", "pearnly_cowork", "pearnly_dms", "pearnly_erp_portal", "pearnly_control")


def lists(hooks):
    found = {}
    for node in ast.parse(hooks.read_text()).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id in ("DESK_JS", "DESK_CSS"):
            found[node.targets[0].id] = list(ast.literal_eval(node.value))
    return found


def bundle_name(app, kind):
    return f"{app}_desk.bundle.{kind}"


def prepare(root, app):
    package = root / app / app
    public = package / "public"
    sources = {}
    for name, files in lists(package / "hooks.py").items():
        if not files:
            continue
        kind = "js" if name == "DESK_JS" else "css"
        if kind == "js":
            entry, line = public / "js" / bundle_name(app, "js"), 'import "../{}";'
        else:
            # 样式照 Frappe 自己的写法：入口是 .bundle.scss、按「App/public/…」引用（打包时入口会被拷到临时目录，相对路径
            # 找不到原文件）；产物在 assets.json 里登记为 <app>_desk.bundle.css。
            # 不带 .css 扩展名：Sass 把以 .css 结尾的 import 当普通 CSS 引用原样留下，不带才会按 Frappe 的搜索路径找到并并进来。
            entry, line = public / "scss" / f"{app}_desk.bundle.scss", '@import "' + app + '/public/{}";'
            files_in = [relative[:-4] if relative.endswith(".css") else relative for relative in files]
        entry.parent.mkdir(parents=True, exist_ok=True)
        entry.write_text("\n".join(line.format(relative) for relative in (files if kind == "js" else files_in)) + "\n")
        sources[bundle_name(app, kind)] = {
            "sources": files, "digests": [sha256((public / relative).read_bytes()).hexdigest() for relative in files]}
    if not sources:
        return False
    (public / "dist").mkdir(parents=True, exist_ok=True)
    (public / "dist" / "desk-sources.json").write_text(json.dumps(sources, indent=1, sort_keys=True))
    return True


def main():
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "apps"
    for app in APPS:
        if (root / app / app / "hooks.py").is_file() and prepare(root, app):
            print(app)


if __name__ == "__main__":
    main()
