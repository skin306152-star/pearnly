"""产品地图：用户能碰到的入口清单（评估与审定见 docs/work/2026-11-04-product-map.md）。

本模块只做三件事，闸门（scripts/check-product-map.py）与部署脚本共用：
- `discover()`：从代码枚举入口（锚点），一类一个发现器，不按某个页面、某个租户单独写；
- `load()`：读地图文件 scripts/impact/product-map/*.toml 与旁边的 not_entries.toml、config.toml、untested_baseline.txt；
- `validate()` / `status()`：地图行本身的格式与引用是否成立、每行每端的测试强度（绿 / 黄 / 真机 / 红）。

纯离线：只读 tracked 文件，不导入产品代码、不连网络。
"""
from __future__ import annotations

import ast
import fnmatch
import re
import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAP_DIR = ROOT / "scripts" / "impact" / "product-map"
SHARED_PARTS = ROOT / "scripts" / "impact" / "shared-parts.toml"
FLOWS = ROOT / "tests" / "flows" / "registry.toml"

# 一期范围：ERP 与 Cowork（DMS、控制面第二期）
APPS = {"pearnly_erp_portal": "ERP", "pearnly_cowork": "Cowork"}
CODE = {"ERP": "erp", "Cowork": "cowork"}
PRODUCTS = {"ERP", "Cowork", "DMS", "控制面", "共用"}
CHANNELS = {"desk", "line", "cc-chat", "cc-operator", "cc-desktop"}
ENDS = ("desktop", "mobile")
KINDS = {"nav-page", "page-action", "page", "report", "line-menu", "line-postback", "line-link", "line-event",
         "line-message", "liff-page", "cc-tool", "cc-operator-hook", "cc-view", "cc-action"}
# 还没有可扫声明的类型：行照登记、锚点暂不核（F 块在 frontend/assistant 加 `// map:` 注释后去掉）
UNSCANNED_KINDS = {"cc-action"}
PROBES = {"liff", "http", "assistant-gate"}
CONFIG_CLASSES = {"same", "shape", "presence", "varies", "infra"}
ROW_FIELDS = {"id", "product", "products", "channel", "ends", "kind", "anchor", "where", "parts", "config", "tests",
              "device", "device_only", "must_verify", "probe", "group", "na", "incidents", "note"}
CONFIG_REF = re.compile(r"^(site|liff|line|feature|kc|ext):\S+$")
FLOW_VIEWPORTS = {"desktop": "desktop", "mobile": "mobile"}


def tracked(root=ROOT) -> list[str]:
    out = subprocess.run(["git", "ls-files"], cwd=root, check=True, capture_output=True, text=True).stdout
    return [line for line in out.splitlines() if line]


def is_test(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return path.startswith("tests/") or "/tests/" in path or name.startswith("test_") or name.startswith("test-") \
        or "/patches/" in path


def _match(path: str, pattern: str) -> bool:
    return fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(path, pattern.replace("**/", ""))


# ---------------------------------------------------------------- 发现

@dataclass
class Found:
    anchor: str
    kind: str
    product: str
    channel: str
    ends: list
    files: list
    where: str = ""


class Repo:
    """一次读完 tracked 文件，发现器共用。"""

    def __init__(self, root=ROOT, files=None):
        self.root = Path(root)
        self.files = files if files is not None else tracked(self.root)
        self.present = set(self.files)
        self._text = {}

    def text(self, path: str) -> str:
        if path not in self._text:
            try:
                self._text[path] = (self.root / path).read_text(encoding="utf-8", errors="ignore")
            except OSError:
                self._text[path] = ""
        return self._text[path]

    def tree(self, path: str):
        return ast.parse(self.text(path))

    def glob(self, pattern: str) -> list[str]:
        return [path for path in self.files if _match(path, pattern)]


def _assigned(tree, name):
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == name for t in node.targets):
            return node.value
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == name and node.value is not None:
            return node.value
    return None


def hooks_value(repo: Repo, app: str, name: str):
    node = _assigned(repo.tree(f"apps/{app}/{app}/hooks.py"), name)
    return ast.literal_eval(node) if node is not None else None


def _module_file(repo: Repo, dotted: str) -> str:
    """`pearnly_x.a.b` → `apps/pearnly_x/pearnly_x/a/b.py`（或包的 __init__.py）；找不到返回空串。"""
    app, *rest = dotted.split(".")
    base = f"apps/{app}/{app}/" + "/".join(rest)
    for candidate in (base + ".py", base + "/__init__.py"):
        if candidate in repo.present:
            return candidate
    return ""


def _impact_entries(repo: Repo) -> list:
    if not SHARED_PARTS.exists() and "scripts/impact/shared-parts.toml" not in repo.present:
        return []
    return tomllib.loads(repo.text("scripts/impact/shared-parts.toml")).get("entry", [])


def _page_files(repo: Repo, page: str, product: str | None, impact: list) -> list:
    stem = page.replace("-", "_")
    files = [p for p in repo.files if p.endswith(f"/page/{stem}/{stem}.js")]
    for entry in impact:
        if entry.get("url") == f"/desk/{page}" and (product is None or entry.get("product") == product):
            files.extend(entry.get("files", []))
    return sorted(dict.fromkeys(files))


def discover_desk(repo: Repo) -> list[Found]:
    impact = _impact_entries(repo)
    found, nav_pages = [], set()
    for app, product in APPS.items():
        code = CODE[product]
        hooks = f"apps/{app}/{app}/hooks.py"
        navigation = (hooks_value(repo, app, "pearnly_navigation") or [{}])[0]
        for page in navigation.get("pages", []):
            name = page[1]
            nav_pages.add(name)
            found.append(Found(f"nav:{code}:{name}", "nav-page", product, "desk", list(ENDS),
                               _page_files(repo, name, product, impact) or [hooks], f"侧栏「{name}」"))
        for page, codes in navigation.get("actions", {}).items():
            files = _page_files(repo, page, product, impact) if page.startswith("pearnly-") else [hooks]
            for action in codes:
                found.append(Found(f"action:{code}:{page}:{action}", "page-action", product, "desk", list(ENDS),
                                   files or [hooks], f"{page} 页头「{action}」"))
    for app in ("pearnly_dms",):  # DMS 二期入图；它导航里的页不算 ERP / Cowork 的「不在导航的页」
        if f"apps/{app}/{app}/hooks.py" in repo.present:
            for page in (hooks_value(repo, app, "pearnly_navigation") or [{}])[0].get("pages", []):
                nav_pages.add(page[1])
    for path in repo.files:
        match = re.match(r"apps/(pearnly_core|pearnly_erp_portal|pearnly_cowork)/.+/page/([a-z_]+)/\2\.json$", path)
        if not match:
            continue
        page = match.group(2).replace("_", "-")
        if page in nav_pages:
            continue
        owners = {e.get("product") for e in impact if e.get("url") == f"/desk/{page}"} & set(CODE)
        product = owners.pop() if len(owners) == 1 else APPS.get(match.group(1), "共用")
        code = CODE.get(product, "shared")
        found.append(Found(f"page:{code}:{page}", "page", product, "desk", list(ENDS),
                           _page_files(repo, page, None, impact) or [path], f"页面 /desk/{page}（不在侧栏）"))
    for app, product in APPS.items():
        for path in repo.glob(f"apps/{app}/**/report/*/*.json"):
            name = path.rsplit("/", 2)[-2]
            if path.endswith(f"/{name}/{name}.json"):
                files = [p for p in repo.files if p.startswith(path.rsplit("/", 1)[0] + "/") and not is_test(p)]
                found.append(Found(f"report:{CODE[product]}:{name}", "report", product, "desk", list(ENDS),
                                   sorted(files), f"报表 {name}"))
    return found


LINE_FILES = {
    "ERP": ["apps/pearnly_erp_portal/pearnly_erp_portal/integrations/line_*.py",
            "apps/pearnly_erp_portal/pearnly_erp_portal/integrations/handlers.py",
            "apps/pearnly_erp_portal/pearnly_erp_portal/integrations/intake.py"],
    "Cowork": ["apps/pearnly_cowork/pearnly_cowork/line/*.py"],
}
MESSAGE_TYPES = ("text", "image", "file", "video", "audio", "sticker", "location")


POSTBACK_KEYS = ("a", "action")
# 处理器里的分发事件不单列：postback / message 由各个按钮与消息类型的行代表
DISPATCH_EVENTS = {"postback", "message"}


def _postback_action(value: str) -> str:
    """postback 数据里的动作键：`a=mode:purchase` → mode，`a=erp&s=cancel` → erp，`action=cowork_erp_start` → cowork_erp_start。"""
    key, _, rest = value.partition("=")
    return re.split(r"[&:%]", rest, 1)[0] if key in POSTBACK_KEYS else ""


def discover_line(repo: Repo) -> list[Found]:
    found = {}

    def add(anchor, kind, product, path, where, ends=("mobile",)):
        item = found.setdefault(anchor, Found(anchor, kind, product, "line", list(ends), [], where))
        if path not in item.files:
            item.files.append(path)

    for product, patterns in LINE_FILES.items():
        code = CODE[product]
        paths = sorted({p for pattern in patterns for p in repo.glob(pattern) if not is_test(p)})
        for path in paths:
            tree = repo.tree(path)
            consts = {t.id: n.value.value for n in tree.body if isinstance(n, ast.Assign)
                      for t in n.targets if isinstance(t, ast.Name)
                      and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str)}
            functions = _assigned(tree, "FUNCTIONS")
            if functions is not None and path.endswith("menu.py"):
                for name in ast.literal_eval(functions):
                    add(f"line:{code}:menu:{name}", "line-menu", product, path, f"LINE 富菜单「{name}」格")
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                        and node.value.startswith(tuple(k + "=" for k in POSTBACK_KEYS)):
                    action = _postback_action(node.value)
                    if action:
                        add(f"line:{code}:postback:{action}", "line-postback", product, path,
                            f"LINE 按钮（postback {action}）")
                elif isinstance(node, ast.Dict):
                    for key, value in zip(node.keys, node.values):
                        if isinstance(key, ast.Constant) and key.value in POSTBACK_KEYS and isinstance(value, ast.Constant) \
                                and isinstance(value.value, str):
                            add(f"line:{code}:postback:{value.value}", "line-postback", product, path,
                                f"LINE 按钮（postback {value.value}）")
                elif isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", "")) == "entry_url" \
                        and node.args and not path.endswith("menu.py"):  # 富菜单格打开的网页由菜单格那一行代表
                    route = _route(node.args[0], consts)
                    if route:
                        add(f"line:{code}:link:{route}", "line-link", product, path, f"LINE 里打开网页 {route}")
                elif isinstance(node, ast.Compare) and len(node.comparators) == 1:
                    left, right = node.left, node.comparators[0]
                    if isinstance(left, ast.Name) and left.id == "etype" and isinstance(right, ast.Constant) \
                            and right.value not in DISPATCH_EVENTS:
                        add(f"line:{code}:event:{right.value}", "line-event", product, path, f"LINE 事件 {right.value}")
                    elif _is_message_type(left):
                        values = [right] if isinstance(right, ast.Constant) else \
                            list(right.elts) if isinstance(right, (ast.Tuple, ast.List, ast.Set)) else []
                        for value in values:
                            if isinstance(value, ast.Constant) and value.value in MESSAGE_TYPES:
                                add(f"line:{code}:message:{value.value}", "line-message", product, path,
                                    f"LINE 里发{value.value}")
        for path in repo.glob(f"apps/{_app_of(product)}/{_app_of(product)}/www/**/*.html"):
            route = "/" + path.split("/www/", 1)[1][:-len(".html")]
            add(f"liff:{code}:{route}", "liff-page", product, path, f"LINE 里的网页 {route}")
    return list(found.values())


def _app_of(product: str) -> str:
    return next(app for app, name in APPS.items() if name == product)


def _is_message_type(node) -> bool:
    """`message.get("type")` 或 `media_type`。"""
    if isinstance(node, ast.Name):
        return node.id == "media_type"
    return isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "get" and node.args \
        and isinstance(node.args[0], ast.Constant) and node.args[0].value == "type"


def _route(node, consts) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return consts.get(node.id, "")
    if isinstance(node, ast.JoinedStr):
        parts = []
        for value in node.values:
            if isinstance(value, ast.Constant):
                parts.append(str(value.value))
            else:
                inner = value.value
                parts.append("{" + (inner.id if isinstance(inner, ast.Name) else "x") + "}")
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _route(node.left, consts), _route(node.right, consts)
        return (left or "") + (right or "{x}") if left else ""
    return ""


def _tool_names(repo: Repo, path: str, variable: str) -> list[str]:
    node = _assigned(repo.tree(path), variable)
    if node is None:
        return []
    names = []
    for item in getattr(node, "elts", []):
        if isinstance(item, ast.Dict):
            for key, value in zip(item.keys, item.values):
                if isinstance(key, ast.Constant) and key.value == "name" and isinstance(value, ast.Constant):
                    names.append(value.value)
    return names


CORE_TOOLS = ("apps/pearnly_core/pearnly_core/assistant/tools.py", "CORE_TOOLS")
CC_VIEWS = "frontend/assistant/src/lib/nav.ts"
CC_SHELL = "frontend/assistant/src/assistant.tsx"


def discover_cc(repo: Repo) -> list[Found]:
    found = []
    for app, product in APPS.items():
        code = CODE[product]
        declared = [(CORE_TOOLS[0], CORE_TOOLS[1])]
        for dotted in hooks_value(repo, app, "pearnly_assistant_tools") or []:
            module, variable = dotted.rsplit(".", 1)
            path = _module_file(repo, module)
            if path:
                declared.append((path, variable))
        for path, variable in declared:
            for name in _tool_names(repo, path, variable):
                channel = "cc-operator" if name.startswith("express_operator") else "cc-chat"
                found.append(Found(f"tool:{code}:{name}", "cc-tool", product, channel, list(ENDS), [path],
                                   f"CC 工具 {name}"))
        for key, dotted in (hooks_value(repo, app, "pearnly_express_operator") or {}).items():
            path = _module_file(repo, dotted.rsplit(".", 1)[0])
            found.append(Found(f"operator:{code}:{key}", "cc-operator-hook", product, "cc-operator", ["desktop"],
                               [path] if path else [f"apps/{app}/{app}/hooks.py"], f"代录 {key}"))
        if CC_VIEWS in repo.present:
            match = re.search(r"export type View\s*=\s*([^;]+);", repo.text(CC_VIEWS))
            for view in re.findall(r'"(\w+)"', match.group(1) if match else ""):
                channel = "cc-operator" if view == "operator" else "cc-chat"
                found.append(Found(f"ccview:{code}:{view}", "cc-view", product, channel, list(ENDS),
                                   [CC_SHELL, CC_VIEWS], f"CC 视图 {view}"))
    return found


def discover(repo: Repo | None = None) -> dict[str, Found]:
    repo = repo or Repo()
    result = {}
    for item in discover_desk(repo) + discover_line(repo) + discover_cc(repo):
        result.setdefault(item.anchor, item)
    return result


# ---------------------------------------------------------------- site_config 键

CONFIG_SCAN = ("apps/pearnly_core/**/*.py", "apps/pearnly_erp_portal/**/*.py", "apps/pearnly_cowork/**/*.py")


def discover_config(repo: Repo | None = None) -> tuple[dict, dict]:
    """站点配置里读到的键 {键: [文件…]} 与按前缀拼出来读的整族 {前缀: [文件…]}。"""
    repo = repo or Repo()
    keys, families = {}, {}
    for path in sorted({p for pattern in CONFIG_SCAN for p in repo.glob(pattern) if not is_test(p)}):
        text = repo.text(path)
        if "conf" not in text and "site_config" not in text:
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        consts = {t.id: n.value.value for n in tree.body if isinstance(n, ast.Assign) for t in n.targets
                  if isinstance(t, ast.Name) and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str)}

        def note(arg):
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                keys.setdefault(arg.value, []).append(path)
            elif isinstance(arg, ast.Name) and arg.id in consts:
                keys.setdefault(consts[arg.id], []).append(path)
            elif isinstance(arg, ast.JoinedStr) and arg.values and isinstance(arg.values[0], ast.Constant):
                families.setdefault(arg.values[0].value, []).append(path)

        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and node.args:
                func = node.func
                name = getattr(func, "attr", getattr(func, "id", ""))
                owner = getattr(func, "value", None)
                if name == "get" and _is_conf(owner):
                    note(node.args[0])
                elif name == "update_site_config":
                    note(node.args[0])
            elif isinstance(node, ast.Subscript) and _is_conf(node.value):
                note(node.slice)
            elif isinstance(node, ast.Attribute) and _is_conf(node.value) and node.attr.startswith("pearnly_"):
                keys.setdefault(node.attr, []).append(path)
    keys = {k: sorted(set(v)) for k, v in keys.items() if k.startswith("pearnly_") or k in ("maintenance_mode", "host_name")}
    return keys, {k: sorted(set(v)) for k, v in families.items() if k.startswith("pearnly_")}


def _is_conf(node) -> bool:
    """`frappe.conf`、`frappe.local.conf`、`conf`、`frappe.get_site_config()`。"""
    if isinstance(node, ast.Attribute) and node.attr == "conf":
        return True
    if isinstance(node, ast.Name) and node.id == "conf":
        return True
    return isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", "")) == "get_site_config"


# ---------------------------------------------------------------- 地图文件

@dataclass
class Row:
    id: str
    product: str
    channel: str
    ends: list
    kind: str
    anchor: str
    where: str
    products: list = field(default_factory=list)
    parts: list = field(default_factory=list)
    config: list = field(default_factory=list)
    tests: list = field(default_factory=list)
    device: str = ""
    device_only: str = ""
    must_verify: bool = False
    probe: dict = field(default_factory=dict)
    group: str = ""
    na: dict = field(default_factory=dict)
    incidents: list = field(default_factory=list)
    note: str = ""
    source: str = ""


@dataclass
class Map:
    rows: dict
    not_entries: dict           # 锚点 → 原因
    config: dict                # 键或前缀族 → {class, note, ...}
    baseline: list              # "<行 id>@<端>"
    errors: list


def load(directory=MAP_DIR) -> Map:
    directory = Path(directory)
    rows, errors = {}, []
    anchors = {}
    for path in sorted(directory.glob("*.toml")):
        if path.name in ("not_entries.toml", "config.toml"):
            continue
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        for raw in data.get("row", []):
            unknown = set(raw) - ROW_FIELDS
            if unknown:
                errors.append(f"{path.name}：行 {raw.get('id')!r} 有不认识的字段 {sorted(unknown)}")
                continue
            try:
                row = Row(**raw, source=path.name)
            except TypeError as error:
                errors.append(f"{path.name}：行 {raw.get('id')!r} 格式不对：{error}")
                continue
            if row.id in rows:
                errors.append(f"行 id 重复：{row.id}（{rows[row.id].source}、{path.name}）")
            if row.anchor in anchors:
                errors.append(f"锚点重复：{row.anchor}（行 {anchors[row.anchor]}、{row.id}）")
            rows[row.id] = row
            anchors[row.anchor] = row.id
    not_entries, config = {}, {}
    if (directory / "not_entries.toml").exists():
        for raw in tomllib.loads((directory / "not_entries.toml").read_text(encoding="utf-8")).get("anchor", []):
            if not str(raw.get("reason") or "").strip():
                errors.append(f"not_entries.toml：{raw.get('anchor')} 没写原因")
            not_entries[raw.get("anchor")] = raw.get("reason", "")
    if (directory / "config.toml").exists():
        data = tomllib.loads((directory / "config.toml").read_text(encoding="utf-8"))
        for name, spec in {**data.get("key", {}), **{k + "*": v for k, v in data.get("family", {}).items()}}.items():
            if spec.get("class") not in CONFIG_CLASSES:
                errors.append(f"config.toml：{name} 的 class 只能是 {sorted(CONFIG_CLASSES)}")
            config[name] = spec
    baseline = []
    if (directory / "untested_baseline.txt").exists():
        baseline = [line.strip() for line in (directory / "untested_baseline.txt").read_text(encoding="utf-8").splitlines()
                    if line.strip() and not line.startswith("#")]
    return Map(rows, not_entries, config, baseline, errors)


def test_sources(root=ROOT):
    """引用得到的测试：流程登记表 {id: 流程}、shared-parts 入口 {id: 入口}。"""
    flows = {f["id"]: f for f in tomllib.loads((Path(root) / "tests/flows/registry.toml").read_text(encoding="utf-8"))["flow"]}
    impact = {e["id"]: e for e in tomllib.loads((Path(root) / "scripts/impact/shared-parts.toml").read_text(encoding="utf-8"))["entry"]}
    parts = {p["id"] for p in tomllib.loads((Path(root) / "scripts/impact/shared-parts.toml").read_text(encoding="utf-8"))["part"]}
    return flows, impact, parts


def validate(product_map: Map, repo: Repo, flows: dict, impact: dict, parts: set) -> list[str]:
    """地图行自身：取值、引用（流程 / 入口 / 共用件 / 单测文件与名字）、真机行与上线必验的要求。锚点与代码是否对上由闸门判。"""
    errors = []
    for row in product_map.rows.values():
        where = f"行 {row.id}（{row.source}）"
        if row.product not in PRODUCTS:
            errors.append(f"{where}：product 只能是 {sorted(PRODUCTS)}")
        if row.channel not in CHANNELS:
            errors.append(f"{where}：channel 只能是 {sorted(CHANNELS)}")
        if row.kind not in KINDS:
            errors.append(f"{where}：kind 只能是 {sorted(KINDS)}")
        if not row.ends or set(row.ends) - set(ENDS):
            errors.append(f"{where}：ends 只能是 {list(ENDS)} 的非空子集")
        if not row.where.strip():
            errors.append(f"{where}：where 要写人话的位置")
        for part in row.parts:
            if part not in parts:
                errors.append(f"{where}：共用件 {part} 不在 shared-parts.toml")
        for ref in row.config:
            if not CONFIG_REF.match(ref):
                errors.append(f"{where}：配置依赖 {ref!r} 要写成 site: / liff: / line: / feature: / kc: / ext: 开头")
        for ref in row.tests:
            problem = _test_ref_problem(ref, repo, flows, impact)
            if problem:
                errors.append(f"{where}：测试 {ref!r} {problem}")
        if row.device_only and not row.device.strip():
            errors.append(f"{where}：标了只能真机（device_only）就要写 device（步骤与预期）")
        if row.device and not row.device_only:
            errors.append(f"{where}：真机清单只对只能真机的行有效——写 device_only（不能自动化的理由），或改成自动测试")
        if row.probe and row.probe.get("type") not in PROBES:
            errors.append(f"{where}：probe.type 只能是 {sorted(PROBES)}")
    return errors


def _test_ref_problem(ref: str, repo: Repo, flows: dict, impact: dict) -> str:
    kind, _, value = ref.partition(":")
    if kind == "flow":
        flow_id, _, end = value.partition("@")
        if flow_id not in flows:
            return "引用的流程不在 tests/flows/registry.toml"
        if end and end not in FLOW_VIEWPORTS:
            return "端只能写 @desktop / @mobile"
        if end and end not in flows[flow_id].get("viewports", []):
            return f"这条流程不跑 {end}"
        return ""
    if kind == "impact":
        return "" if value in impact else "引用的入口不在 shared-parts.toml"
    if kind == "unit":
        path, _, name = value.partition("::")
        if path not in repo.present:
            return "测试文件不存在"
        if name and not re.search(r"\b" + re.escape(name) + r"\b", repo.text(path)):
            return f"测试文件里找不到 {name}"
        return ""
    return "只能是 flow: / impact: / unit: 开头"


GREEN, YELLOW, DEVICE, RED = "绿", "黄", "真机", "红"


def status(row: Row, flows: dict, impact: dict) -> dict:
    """每端的测试强度：绿 = 有业务断言（流程、单测、离线入口、读码 / 消息等业务探针）；
    真机 = 标了只能真机、有真机清单；黄 = 只有打开冒烟 / 人工清单；红 = 都没有。只有绿和真机算有测试（主会话 11-04）。"""
    result = {end: RED for end in row.ends}

    def raise_to(end, level):
        order = (RED, YELLOW, DEVICE, GREEN)
        if end in result and order.index(level) > order.index(result[end]):
            result[end] = level

    for ref in row.tests:
        kind, _, value = ref.partition(":")
        if kind == "flow" and value.partition("@")[0] in flows:
            flow_id, _, only = value.partition("@")
            for end in flows[flow_id].get("viewports", []):
                if not only or end == only:
                    raise_to(end, GREEN)
        elif kind == "unit":
            for end in row.ends:
                raise_to(end, GREEN)
        elif kind == "impact" and value in impact:
            entry = impact[value]
            level = GREEN if entry["check"] == "offline" or (entry["check"] == "auto" and entry.get("smoke") != "page") \
                else YELLOW
            for end in row.ends:
                raise_to(end, level)
    if row.device_only and row.device:
        for end in row.ends:
            raise_to(end, DEVICE)
    return result


def tested(level: str) -> bool:
    return level in (GREEN, DEVICE)
