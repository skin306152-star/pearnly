#!/usr/bin/env python3
"""给一台测试机签 CC 桌面版的配对码（本机 Mac 实验栈，在本栈 <产品>-bench 容器里跑；scripts/maclab.sh `mint` 调用）。

走服务端登记电脑的正路，以测试账号的身份：

- ERP（共用机器通道，ADR-025）：``pearnly_core.cc_desktop.api.register``——同 CC 窗口里员工登录 ERP 后对话页登记这台电脑。
  设备密钥用一把随机的（共用通道只凭 ``pcc_`` 令牌认电脑，没有「认电脑」那一步，CC 本机的设备密钥不参与）。每签一次就是一台新电脑。
- Cowork：``pearnly_cowork.companion.api.register``，**不带设备密钥**（旧的「一条连接一个槽位」那条路，管理员第一次签时顺带建
  本站的 Express 连接 CWEC-…）。不带的原因：Cowork 的 CC 桌面版启动后会用本机自己的设备密钥「认电脑」（device_api.hello），
  用随机设备密钥签的令牌会被判成「已被另一台电脑认领」而停用；旧槽位的令牌由第一次 hello 认领到这台电脑上。
  所以 Cowork 栈一次只给一台测试机签（第二台要等那台 hello 过后带它自己的设备密钥签，这里不做）。槽位上已有一把时回 ``in_use``；
  设 ``MACLAB_REPLACE=1`` 走「改用这台电脑」（带上旧钥的代数换新，旧钥立即作废）。已被新版 CC 认领过的槽位回 ``update_needed``，
  那时只能 ``maclab.sh reset cowork --yes`` 后重起。

只往标准输出打两行：``DEVICE <电脑或连接>`` 和 ``PAIRING_CODE <配对码>``（形如 ``pcc_…@erp.maclab.localhost:18991``），
由调用方交给测试机；不写文件、不进日志。

环境变量：MACLAB_SITE、MACLAB_PUBLIC（主机:端口，进配对码的 @ 后面）、MACLAB_USER（以谁的身份签）、MACLAB_LABEL（电脑名）。
"""
import json
import os
import secrets
import sys
import uuid
from pathlib import Path

SITES = Path("/home/frappe/frappe-bench/sites")
SUFFIX = ".maclab.localhost"


def main(product: str) -> int:
    site, public, user = os.environ["MACLAB_SITE"], os.environ["MACLAB_PUBLIC"], os.environ["MACLAB_USER"]
    label = os.environ.get("MACLAB_LABEL") or "maclab"
    if not site.endswith(SUFFIX) or not public.split(":")[0].endswith(SUFFIX):
        raise SystemExit(f"Refusing: {site} / {public} is not a maclab site")
    os.chdir(SITES)
    import frappe

    frappe.init(site=site)
    frappe.connect()
    try:
        if frappe.conf.get("pearnly_product") != product or frappe.conf.get("pearnly_oidc_issuer"):
            raise SystemExit(f"Refusing: {site} is not a blank {product} maclab site")
        frappe.set_user(user)
        if product == "erp":
            from pearnly_core.cc_desktop import api

            result = api.register(request_key=str(uuid.uuid4()), device_key=secrets.token_urlsafe(32), device_name=label)
            holder = result.get("device")
        else:
            from pearnly_cowork.companion import api

            result = api.register(request_key=str(uuid.uuid4()), express=True)
            if result.get("status") == "in_use" and os.environ.get("MACLAB_REPLACE") == "1":
                # 槽位上已有一把（上一台测试机、冒烟时签的）：照「改用这台电脑」那条路带上它的代数换新，旧的立即作废
                result = api.register(request_key=str(uuid.uuid4()), express=True, replace=True,
                                      expected_generation=result.get("generation"))
            holder = result.get("connection")
        frappe.db.commit()
        token = result.get("token") if isinstance(result, dict) else None
        if not token:
            # 不回密钥的结论（in_use / update_needed / no_connection …）原样交回，不带任何密钥
            print("MINT_REFUSED " + json.dumps({key: value for key, value in (result or {}).items() if key != "token"},
                                               default=str), flush=True)
            return 1
        print(f"DEVICE {holder}", flush=True)
        print(f"PAIRING_CODE {token}@{public}", flush=True)
        return 0
    finally:
        frappe.destroy()


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("erp", "cowork"):
        raise SystemExit("usage: mint.py <erp|cowork>")
    sys.exit(main(sys.argv[1]))
