# -*- coding: utf-8 -*-
"""控制面清单。

控制面**只在开发机运行**（ADR-004），不部署到云服务器，也不装入任何租户 Site。
它按 ADR-004 的要求只承担开户与授权管理。
"""

MANIFEST = {
    "product": "control",
    "version": 1,
    "bench_group": "control",
    "entry_prefixes": ["/api/v1/control/", "/pearnly-password"],
    "apps": ["pearnly_core", "pearnly_control"],
    "features": [
        "companies",     # 公司（商业客户）
        "principals",    # 账号
        "invitations",   # 发号
        "deployments",   # 开通：公司 × 产品
        "features_toggle",
        "domains",       # 域名绑定
        "roles",         # 角色与账套范围
        "suspend",       # 暂停与恢复
    ],
    "modules": [],
    "must_not": [
        "在租户 Site 上安装本清单",
        "把平台账号当作业务账号使用",
        "暴露到公网",
    ],
}
