"""CC 桌面版的日志只经 ``logger.get_logger`` 拿（11-09 发布窗口验收）。

日志文件（companion.log / window.log）只挂在 ``pearnly_companion`` 这个日志器上；直接 ``logging.getLogger(__name__)`` 的模块
打包后叫 ``src.companion.<模块>``，不在它下面，写的行哪个文件里都没有（b17f63a16 的「Express 位置改勾」那行就是这样丢的）。
写死 ``"pearnly_companion.xxx"`` 的名字虽然能落盘，也同属一类：统一走 get_logger，由这条检查拦住新代码再直接用。"""
import ast
import logging
from pathlib import Path

from src.companion.logger import get_logger

SRC = Path(__file__).resolve().parents[1] / "src" / "companion"


def _direct_get_logger(path: Path) -> list:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "getLogger" \
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "logging":
            found.append(node.lineno)
        if isinstance(node, ast.ImportFrom) and node.module == "logging" \
                and any(alias.name == "getLogger" for alias in node.names):
            found.append(node.lineno)
    return found


def test_no_module_gets_a_logger_around_the_shared_one():
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        if path.name == "logger.py" and path.parent == SRC:
            continue   # logger.py 自己建 pearnly_companion 与 activity 日志器
        offenders += [f"{path.relative_to(SRC.parent.parent)}:{line}" for line in _direct_get_logger(path)]
    assert offenders == [], "改用 logger.get_logger(__name__)：" + ", ".join(offenders)


def test_get_logger_puts_every_module_under_the_file_logger():
    for name in ("src.companion.profile_store", "companion.profile_store", "src.companion.express_operator.engine",
                 "pearnly_companion.auto_update", "src.companion", "companion", "", "pearnly_companion"):
        logger = get_logger(name)
        assert logger.name == "pearnly_companion" or logger.name.startswith("pearnly_companion."), name
    assert get_logger("src.companion.profile_store") is logging.getLogger("pearnly_companion.profile_store")
