"""甲骨文那台上的外部守望（用户 2026-10-17 定）：每分钟查网站，备份停了或网站打不开就发邮件；每月恢复演练的下载与发信也在这里。

监控必须放在被监控的机器之外：10-16 主服务器卡死约 10 分钟，没有任何告警。只读：只访问公开网址和备份存储桶（只读账号），
不连生产数据库、不登录任何站点。

    watch.py probe                 # 每分钟（pearnly-watch.timer）：网站；每 10 分钟顺带查备份是否按时
    watch.py fetch --dest DIR      # 恢复演练：下载最新一份每日备份和它之后的改动记录
    watch.py mail SUBJECT FILE     # 发一封信（演练报告）

配置：/opt/pearnly-watch/targets.json（要查的网址）；/opt/pearnly-watch/secrets/{smtp.json,backup-reader.json,watch.env}（600）。
"""
import datetime as dt
import json
import os
import smtplib
import sys
import time
import urllib.request
from email.message import EmailMessage
from pathlib import Path

ROOT = Path(os.environ.get("PEARNLY_WATCH_ROOT", "/opt/pearnly-watch"))
STATE = Path(os.environ.get("PEARNLY_WATCH_STATE", "/var/lib/pearnly-watch/state.json"))
BUCKET = os.environ.get("PEARNLY_BACKUP_BUCKET", "pearnly-backups-sg")
BANGKOK = dt.timezone(dt.timedelta(hours=7))
# 网站连续这么多分钟打不开才发信（一两分钟的抖动不吵人）；恢复后发一封「已恢复」。
SITE_FAILURES = 3
# 每日备份曼谷 02:00 开始、1 分钟左右传完；03:00 后还没有当天那份就算没按时。
DAILY_DUE_HOUR = 3
# 改动记录每 5 分钟传一次；超过 20 分钟没有新的一份就算停了。
BINLOG_STALE_MINUTES = 20
# 自己连这个都打不开时，说明是这台的网络问题，不发信。
CONTROL_URL = "https://www.google.com/generate_204"


def http_ok(url, expect=None, timeout=15):
    """(是否正常, 说明)。正常 = 200（或 expect 指定的码），且 contains 文字在正文里（若给了）。"""
    expect = expect or {}
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "pearnly-watch/1"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(4096).decode("utf-8", "replace")
            code = response.status
    except urllib.error.HTTPError as error:
        code, body = error.code, ""
    except Exception as error:  # noqa: BLE001  连不上、超时、证书错误都算打不开
        return False, type(error).__name__
    if code != expect.get("status", 200):
        return False, f"HTTP {code}"
    if expect.get("contains") and expect["contains"] not in body:
        return False, "内容不对"
    return True, f"HTTP {code}"


def step(entry, ok, threshold, now, detail=""):
    """一个检查项的状态推进。返回 (新状态, 事件)：事件为 'down'（该发告警）、'up'（该发已恢复）或 None。"""
    entry = dict(entry or {"failures": 0, "alerted": False})
    if ok:
        event = "up" if entry.get("alerted") else None
        return {"failures": 0, "alerted": False}, event
    entry["failures"] = entry.get("failures", 0) + 1
    entry["detail"] = detail
    entry.setdefault("since", now)
    if not entry.get("alerted") and entry["failures"] >= threshold:
        entry["alerted"] = True
        return entry, "down"
    return entry, None


def daily_due_day(now_bkk):
    """此刻应当已经有的最新一份每日备份的曼谷日期。"""
    day = now_bkk.date()
    return day if now_bkk.hour >= DAILY_DUE_HOUR else day - dt.timedelta(days=1)


def binlog_minutes_behind(names, now_utc):
    """存储桶里改动记录的名字形如 binlog/<日期>/<20261017T081500Z>-binlog.000123.gz；返回最新一份离现在多少分钟。"""
    stamps = []
    for name in names:
        stem = name.rsplit("/", 1)[-1]
        try:
            stamps.append(dt.datetime.strptime(stem.split("-", 1)[0], "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.timezone.utc))
        except ValueError:
            continue
    if not stamps:
        return None
    return int((now_utc - max(stamps)).total_seconds() // 60)


# ── 谷歌存储（只读账号） ──────────────────────────────────────────────────────

def _gcs():
    import requests
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2 import service_account
    key = ROOT / "secrets/backup-reader.json"
    credentials = service_account.Credentials.from_service_account_file(
        str(key), scopes=["https://www.googleapis.com/auth/devstorage.read_only"])
    return AuthorizedSession(credentials), requests


def gcs_list(session, prefix):
    names, token = [], None
    while True:
        params = {"prefix": prefix, "fields": "items(name,size),nextPageToken"}
        if token:
            params["pageToken"] = token
        response = session.get(f"https://storage.googleapis.com/storage/v1/b/{BUCKET}/o", params=params, timeout=60)
        response.raise_for_status()
        data = response.json()
        names += [item["name"] for item in data.get("items", [])]
        token = data.get("nextPageToken")
        if not token:
            return names


def gcs_download(session, name, dest):
    from urllib.parse import quote
    url = f"https://storage.googleapis.com/storage/v1/b/{BUCKET}/o/{quote(name, safe='')}?alt=media"
    with session.get(url, stream=True, timeout=300) as response:
        response.raise_for_status()
        with open(dest, "wb") as handle:
            for chunk in response.iter_content(1024 * 1024):
                handle.write(chunk)


# ── 发信 ─────────────────────────────────────────────────────────────────────

def watch_env():
    """secrets/watch.env：ALERT_TO=收告警的邮箱；CHECK_BINLOG=1 表示生产已经在同步改动记录（上线前不查，免得误报）。"""
    path = ROOT / "secrets/watch.env"
    return dict(line.split("=", 1) for line in path.read_text().splitlines() if "=" in line) if path.exists() else {}


def send_mail(subject, body):
    config = json.loads((ROOT / "secrets/smtp.json").read_text())
    env = watch_env()
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = f"Pearnly 监控 <{config['from']}>"
    message["To"] = env["ALERT_TO"].strip()
    message.set_content(body)
    with smtplib.SMTP(config["host"], int(config["port"]), timeout=30) as smtp:
        smtp.starttls()
        smtp.login(config["user"], config["password"])
        smtp.send_message(message)


# ── 子命令 ───────────────────────────────────────────────────────────────────

def probe():
    now_utc = dt.datetime.now(dt.timezone.utc)
    stamp = now_utc.astimezone(BANGKOK).strftime("%Y-%m-%d %H:%M")
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    if not http_ok(CONTROL_URL, {"status": 204})[0]:
        return 0  # 这台自己断网，不判生产
    mails = []
    for target in json.loads((ROOT / "targets.json").read_text()):
        ok, detail = http_ok(target["url"], target)
        state[target["name"]], event = step(state.get(target["name"]), ok, SITE_FAILURES, stamp, detail)
        if event == "down":
            mails.append((f"Pearnly 告警：{target['name']} 打不开",
                          f"{target['name']}（{target['url']}）从 {state[target['name']]['since']}（曼谷时间）起连续 "
                          f"{SITE_FAILURES} 分钟打不开：{detail}。\n恢复后会再发一封「已恢复」。"))
        elif event == "up":
            mails.append((f"Pearnly 已恢复：{target['name']}", f"{target['name']} 在 {stamp}（曼谷时间）已恢复正常。"))
    if now_utc.minute % 10 == 0:
        mails += check_backups(state, now_utc, stamp)
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False))
    for subject, body in mails:
        send_mail(subject, body)
    return 0


def check_backups(state, now_utc, stamp):
    mails = []
    try:
        session, _ = _gcs()
        day = daily_due_day(now_utc.astimezone(BANGKOK))
        daily_ok = bool(gcs_list(session, f"daily/{day:%Y-%m-%d}/manifest.json"))
        names = []
        for offset in (0, 1):
            names += gcs_list(session, f"binlog/{(now_utc - dt.timedelta(days=offset)):%Y-%m-%d}/")
        behind = binlog_minutes_behind(names, now_utc)
    except Exception as error:  # noqa: BLE001
        daily_ok, behind = None, None
        detail = type(error).__name__
    checks = []
    if daily_ok is not None:
        checks.append(("每日备份", daily_ok, f"{day:%Y-%m-%d} 那一份还没有"))
        if watch_env().get("CHECK_BINLOG", "").strip() == "1":
            checks.append(("改动记录同步", behind is not None and behind <= BINLOG_STALE_MINUTES,
                           "存储桶里找不到改动记录" if behind is None else f"最新一份是 {behind} 分钟前"))
    else:
        checks.append(("备份存储桶", False, f"读不了存储桶：{detail}"))
    for name, ok, detail in checks:
        state[name], event = step(state.get(name), ok, 1, stamp, detail)
        if event == "down":
            mails.append((f"Pearnly 告警：{name}没有按时完成", f"{stamp}（曼谷时间）检查：{detail}。"))
        elif event == "up":
            mails.append((f"Pearnly 已恢复：{name}", f"{name}在 {stamp}（曼谷时间）已恢复正常。"))
    return mails


def fetch(dest):
    """下载最新一份每日备份（数据库、校验清单、密钥包）和它开始之后的全部改动记录；打印备份日期。"""
    session, _ = _gcs()
    dest = Path(dest)
    (dest / "binlog").mkdir(parents=True, exist_ok=True)
    days = sorted({name.split("/")[1] for name in gcs_list(session, "daily/") if name.endswith("/manifest.json")})
    if not days:
        raise SystemExit("no daily backup")
    day = days[-1]
    for name in gcs_list(session, f"daily/{day}/"):
        base = name.rsplit("/", 1)[-1]
        if base.startswith(("db-", "secrets", "SHA256SUMS", "manifest")):
            gcs_download(session, name, dest / base)
    # 每日备份曼谷 02:00 = 前一天 19:00 UTC 开始；从它前一小时起的改动记录都取下来，回放时按导出文件里记的位置截取。
    start = dt.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=BANGKOK) + dt.timedelta(hours=1)
    start_utc = start.astimezone(dt.timezone.utc)
    utc_days = {(start_utc + dt.timedelta(days=offset)).strftime("%Y-%m-%d") for offset in range(0, 3)}
    for utc_day in sorted(utc_days):
        for name in gcs_list(session, f"binlog/{utc_day}/"):
            stem = name.rsplit("/", 1)[-1]
            when = dt.datetime.strptime(stem.split("-", 1)[0], "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.timezone.utc)
            if when >= start_utc:
                gcs_download(session, name, dest / "binlog" / stem)
    print(day)
    return 0


def main(argv):
    if argv[:1] == ["probe"]:
        return probe()
    if argv[:2] == ["fetch", "--dest"] and len(argv) == 3:
        return fetch(argv[2])
    if argv[:1] == ["mail"] and len(argv) == 3:
        send_mail(argv[1], Path(argv[2]).read_text())
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
