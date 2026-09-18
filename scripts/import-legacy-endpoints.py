#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从老产品的 supabase dump 里把 `erp_endpoints` 捞出来并解密。

为什么需要：老产品的 ERP / DMS 连接凭据是 **Fernet 加密存在生产库**里的
（`config.password_enc` / `config.username_enc`，密钥是 env 的 `PEARNLY_KMS_KEY`）。
新平台要"跑真实状态"就得有这些连接 —— 老产品那边已经跑通过，凭据也在本机，
不该再让用户手抄一遍。

**明文不入版本库、不进对话**：解密结果只写到
`~/.config/pearnly/credentials/legacy-endpoints.json`（0600），
终端只打印脱敏摘要（用户名打码、密码只看长度）。

前置：先导出一份该表的 SQL（本机已验证的做法，`pg_restore 15` 读不了 17 的归档，
所以借一个 PG17 容器）：

    docker --context colima-pearnly run --rm \\
      -v "$HOME/.config/pearnly-migration/20260905:/d:ro" \\
      -v "$HOME/.config/pearnly-migration/20260918:/out" postgres:17-alpine \\
      sh -c 'pg_restore -a -t erp_endpoints -f - /d/supabase-before.dump > /out/erp_endpoints.sql'

用法：
    <老产品>/venv/bin/python scripts/import-legacy-endpoints.py \\
        --sql ~/.config/pearnly-migration/20260918/erp_endpoints.sql \\
        --env ~/.config/pearnly-migration/20260905/web.env

需要 `cryptography`（用老产品的 venv 跑最省事，它一定有）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

DEFAULT_OUT = Path.home() / ".config" / "pearnly" / "credentials" / "legacy-endpoints.json"


def read_kms_key(env_path: Path) -> str:
    """从 env 文件里取 `PEARNLY_KMS_KEY`（只认这一个键，别把整份 env 读进来）。"""
    for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line.startswith("PEARNLY_KMS_KEY="):
            key = line.split("=", 1)[1].strip().strip('"').strip("'")
            if key:
                return key
    raise SystemExit("在 %s 里没找到非空的 PEARNLY_KMS_KEY" % env_path)


def unescape_copy_field(raw: str) -> str | None:
    """COPY 文本格式的一格 → 字符串。`\\N` 是 NULL。"""
    if raw == r"\N":
        return None
    out = []
    i = 0
    while i < len(raw):
        ch = raw[i]
        if ch == "\\" and i + 1 < len(raw):
            nxt = raw[i + 1]
            out.append({"t": "\t", "n": "\n", "r": "\r", "\\": "\\"}.get(nxt, nxt))
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def parse_copy(sql_text: str) -> list[dict]:
    """把 `COPY public.erp_endpoints (...) FROM stdin;` 那段解析成 dict 列表。"""
    m = re.search(
        r"^COPY\s+public\.erp_endpoints\s*\(([^)]*)\)\s+FROM\s+stdin;\s*$",
        sql_text,
        re.MULTILINE,
    )
    if not m:
        raise SystemExit("这份 SQL 里没找到 erp_endpoints 的 COPY 段")
    cols = [c.strip().strip('"') for c in m.group(1).split(",")]

    body = sql_text[m.end():]
    rows = []
    for line in body.splitlines():
        if line == r"\.":
            break
        if not line.strip():
            continue
        cells = line.split("\t")
        if len(cells) != len(cols):
            continue  # 不是数据行（比如空行/元命令），跳过而不是猜
        rows.append({c: unescape_copy_field(v) for c, v in zip(cols, cells)})
    return rows


def mask(value: str | None) -> str:
    """脱敏：露头 2 个字符 + 长度，够人对号入座，不够拿去登录。"""
    if not value:
        return "(空)"
    if len(value) <= 3:
        return "*" * len(value)
    return "%s***（%d 位）" % (value[:2], len(value))


def decrypt_or_note(fernet, token: str | None) -> tuple[str, str]:
    """返回 (明文, 说明)。解不开就说解不开，**不静默丢**。"""
    if not token:
        return "", "没存"
    try:
        return fernet.decrypt(token.encode()).decode(), "ok"
    except Exception as exc:  # noqa: BLE001 — 这里就是要吞掉并如实报告
        return "", "解不开(%s)" % type(exc).__name__


def main() -> int:
    ap = argparse.ArgumentParser(description="导入并解密老产品的 erp_endpoints")
    ap.add_argument("--sql", required=True, type=Path, help="erp_endpoints 的 COPY 导出")
    ap.add_argument("--env", required=True, type=Path, help="带 PEARNLY_KMS_KEY 的 env 文件")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()

    try:
        from cryptography.fernet import Fernet
    except ImportError:
        print("缺 cryptography —— 用老产品的 venv 跑：<老产品>/venv/bin/python", file=sys.stderr)
        return 1

    key = read_kms_key(args.env)
    fernet = Fernet(key.encode() if isinstance(key, str) else key)

    rows = parse_copy(args.sql.read_text(encoding="utf-8", errors="replace"))

    endpoints = []
    for r in rows:
        try:
            cfg = json.loads(r.get("config") or "{}")
        except ValueError:
            cfg = {}
        user, u_note = decrypt_or_note(fernet, cfg.get("username_enc"))
        pwd, p_note = decrypt_or_note(fernet, cfg.get("password_enc"))
        # DMS 那套连接还多一组"有修改权限的账号"（建/改客户要它），一并解出来
        admin_user, au_note = decrypt_or_note(fernet, cfg.get("admin_username_enc"))
        admin_pwd, ap_note = decrypt_or_note(fernet, cfg.get("admin_password_enc"))
        endpoints.append({
            "id": r.get("id"),
            "user_id": r.get("user_id"),
            "name": r.get("name"),
            "adapter": r.get("adapter"),
            "enabled": r.get("enabled") == "t",
            "auto_push": r.get("auto_push") == "t",
            "is_default": r.get("is_default") == "t",
            "last_status": r.get("last_status"),
            "created_at": r.get("created_at"),
            "tenant_id": r.get("tenant_id"),
            # 非密文的连接参数原样留（seldb/comidyear/system_url 之类）
            "config_plain": {k: v for k, v in cfg.items()
                             if k not in ("username_enc", "password_enc")},
            "username": user,
            "password": pwd,
            "admin_username": admin_user,
            "admin_password": admin_pwd,
            "_decrypt": {
                "username": u_note, "password": p_note,
                "admin_username": au_note, "admin_password": ap_note,
            },
        })

    args.out.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({"source_sql": str(args.sql), "endpoints": endpoints},
                  fh, ensure_ascii=False, indent=2)

    # ── 终端只给脱敏摘要 ───────────────────────────────────────────────
    by_adapter: dict[str, int] = {}
    for e in endpoints:
        by_adapter[e["adapter"]] = by_adapter.get(e["adapter"], 0) + 1

    print("读完 %d 条端点，按类型：" % len(endpoints))
    for a, n in sorted(by_adapter.items()):
        print("  %-12s %d" % (a, n))
    print()
    def cred_state(e):
        core = e["_decrypt"]["username"] == "ok" and e["_decrypt"]["password"] == "ok"
        admin = e["_decrypt"]["admin_username"] in ("ok", "没存")
        return "主+admin" if (core and admin and e["admin_username"]) else ("主" if core else "缺")

    print("%-38s %-16s %-6s %-8s %s" % ("id", "adapter", "启用", "凭据", "用户名(脱敏)"))
    for e in endpoints:
        print("%-38s %-16s %-6s %-8s %s" % (
            e["id"], e["adapter"], "是" if e["enabled"] else "否",
            cred_state(e), mask(e["username"])))

    ok = sum(1 for e in endpoints
             if e["_decrypt"]["username"] == "ok" and e["_decrypt"]["password"] == "ok")
    print()
    print("解密成功 %d / %d" % (ok, len(endpoints)))
    print("明文已写到 %s（0600，不入版本库）" % args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
