"""每一份 MariaDB 定义都关掉快照隔离（11-23，并发取号 1020）。

MariaDB 11.8 默认 ``innodb_snapshot_isolation=ON``：事务读过之后，加锁读或改到别人刚提交的行就报 1020。生产、实验栈、
测试环境、候选车道都要同一行为，否则在实验栈测不出线上的并发表现（或反过来）。

规则：``infrastructure/`` 下每个 compose 文件里，镜像是 ``mariadb:`` 的服务，或 ``extends`` 生产 ``mariadb`` 服务的，
- 自己写了 ``command`` 的，里面必须有 ``--innodb-snapshot-isolation=OFF``；
- 没写 ``command`` 的，只允许是 ``extends`` 生产那份（继承它的 command）。
不依赖 PyYAML：按缩进切服务块。
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FLAG = "--innodb-snapshot-isolation=OFF"


def services(text: str):
    """(服务名, 块文本)：``services:`` 下两格缩进的键。"""
    lines = text.splitlines()
    out, name, block, inside = [], None, [], False
    for line in lines:
        if re.match(r"^services:\s*$", line):
            inside = True
            continue
        if inside and re.match(r"^\S", line):
            inside = False
        if not inside:
            continue
        m = re.match(r"^  ([A-Za-z0-9_.-]+):\s*(#.*)?$", line)
        if m:
            if name:
                out.append((name, "\n".join(block)))
            name, block = m.group(1), []
        elif name:
            block.append(line)
    if name:
        out.append((name, "\n".join(block)))
    return out


def command_text(block: str) -> str | None:
    """``command:`` 的内容（行内列表或下面的 ``- `` 列表，含续行）；没有返回 None。"""
    lines = block.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"^(\s+)command:\s*(.*)$", line)
        if not m:
            continue
        indent, parts = len(m.group(1)), [m.group(2)]
        for nxt in lines[i + 1:]:
            if nxt.strip() and len(nxt) - len(nxt.lstrip()) <= indent:
                break
            parts.append(nxt)
        return "\n".join(parts)
    return None


def mariadb_services():
    found = []
    for path in sorted((ROOT / "infrastructure").rglob("*.yml")):
        text = path.read_text(encoding="utf-8")
        for name, block in services(text):
            image = re.search(r"^\s+image:\s*['\"]?mariadb:", block, re.M)
            extends_prod = re.search(r"extends:.*production/compose\.yml.*service:\s*mariadb", block) or (
                re.search(r"extends:", block) and re.search(r"file:\s*\.\./production/compose\.yml", block)
                and re.search(r"service:\s*mariadb\b", block))
            if image or extends_prod:
                found.append((path.relative_to(ROOT).as_posix(), name, block, bool(extends_prod)))
    return found


class SnapshotIsolationOff(unittest.TestCase):
    def test_every_mariadb_turns_snapshot_isolation_off(self):
        found = mariadb_services()
        names = {(f, n) for f, n, _, _ in found}
        # 防止切块逻辑失效后什么都没查到
        self.assertIn(("infrastructure/production/compose.yml", "mariadb"), names)
        self.assertIn(("infrastructure/compose/regression-lab.yml", "db"), names)
        self.assertGreaterEqual(len(found), 10)
        bad = []
        for path, name, block, extends_prod in found:
            command = command_text(block)
            if command is None:
                if not extends_prod:
                    bad.append(f"{path}:{name} 没有 command，默认快照隔离是 ON")
            elif FLAG not in command:
                bad.append(f"{path}:{name} 的 command 没有 {FLAG}")
        self.assertEqual(bad, [])

    def test_parser_reads_both_command_shapes(self):
        block = "    image: mariadb:11.8\n    command:\n      - --a\n      - " + FLAG + "\n    volumes: [x]"
        self.assertIn(FLAG, command_text(block))
        self.assertNotIn("volumes", command_text(block))
        self.assertIsNone(command_text("    image: mariadb:11.8\n"))


if __name__ == "__main__":
    unittest.main()
