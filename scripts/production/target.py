"""Public deployment destination; credentials remain outside the repository."""
import ipaddress
import json
from pathlib import Path
import re


def load_target():
    path = Path(__file__).resolve().parents[2] / "infrastructure/production/target.json"
    target = json.loads(path.read_text())
    ipaddress.IPv4Address(target["public_ipv4"])
    ipaddress.IPv6Address(target["public_ipv6"])
    ipaddress.ip_address(target["ssh_host"])
    if not re.fullmatch(r"production-[a-z0-9-]+", target["target_name"]):
        raise ValueError("Invalid production target")
    return target


def ssh_command(key):
    return ["ssh", "-i", str(Path.home() / ".config/pearnly-production" / key),
            "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=10",
            "root@" + load_target()["ssh_host"]]


if __name__ == "__main__":
    print(load_target()["ssh_host"])
