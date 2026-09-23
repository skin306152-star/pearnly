"""Exercise native SQL/public/private backup restore on only the DMS release lab.

This intentionally replaces this disposable Site database with its own backup.
It cannot target an argument, external host, or a Site without the lab marker.
"""
import os
from pathlib import Path
import subprocess
import uuid

import frappe
from frappe.utils.backups import new_backup
from frappe.utils.file_manager import save_file


SITE = "release-dms.localhost"
TOKEN = f"unified-release-lab-backup-sentinel-{uuid.uuid4()}"
PASSWORD = "unified-release-lab-only"
SITE_ROOT = Path("/home/frappe/frappe-bench/sites")


def connect():
    os.chdir(SITE_ROOT)
    # Match the native bench command context; absolute sites_path changes the
    # tar member prefix and is not the backup CLI's supported layout.
    frappe.init(site=SITE)
    frappe.connect()
    if (not frappe.conf.pearnly_release_lab or frappe.conf.db_host != "db"
            or not frappe.conf.disable_scheduler):
        raise RuntimeError("Refusing a foreign or active Site")
    frappe.set_user("Administrator")


def main():
    if os.environ.get("PEARNLY_RELEASE_LAB") != "1":
        raise SystemExit("Requires dedicated release laboratory")
    if not (SITE_ROOT / ".unified-release-lab").is_file():
        raise SystemExit("Missing release laboratory volume marker")
    connect()
    todo = frappe.get_doc({"doctype": "ToDo", "description": TOKEN}).insert()
    public = save_file("release-backup-public.txt", TOKEN.encode(), "ToDo", todo.name,
                       is_private=0)
    private = save_file("release-backup-private.txt", TOKEN.encode(), "ToDo", todo.name,
                        is_private=1)
    todo_name = todo.name
    files = [public.name, private.name]
    paths = [Path(public.get_full_path()), Path(private.get_full_path())]
    frappe.db.commit()
    assert all(path.read_bytes() == TOKEN.encode() for path in paths)
    backup = new_backup(ignore_files=False, force=True)
    backup_paths = [str(Path(value).resolve()) for value in (
        backup.backup_path_db, backup.backup_path_files, backup.backup_path_private_files,
    )]
    frappe.db.set_value("ToDo", todo_name, "description", "changed-after-backup")
    frappe.db.commit()
    for path in paths:
        path.write_bytes(b"changed-after-backup")
    assert frappe.db.get_value("ToDo", todo_name, "description") != TOKEN
    assert all(path.read_bytes() != TOKEN.encode() for path in paths)
    frappe.destroy()
    result = subprocess.run(
        ["bench", "--site", SITE, "restore", backup_paths[0],
         "--db-root-password", PASSWORD, "--with-public-files", backup_paths[1],
         "--with-private-files", backup_paths[2]],
        input="y\n", text=True, capture_output=True, cwd=SITE_ROOT.parent,
    )
    if result.returncode:
        print(result.stdout[-10000:])
        print(result.stderr[-10000:])
        result.check_returncode()
    connect()
    assert frappe.db.get_value("ToDo", todo_name, "description") == TOKEN
    assert all(frappe.get_doc("File", name).get_content() in (TOKEN, TOKEN.encode()) for name in files)
    installed = frappe.get_installed_apps()
    assert "pearnly_dms" in installed and "erpnext" not in installed
    frappe.destroy()
    print(f"PASS {SITE}: native SQL + public files + private files backup/restore roundtrip")
    print("Backup paths retained only in the task-owned Site volume:")
    for path in backup_paths:
        print(path)


if __name__ == "__main__":
    main()
