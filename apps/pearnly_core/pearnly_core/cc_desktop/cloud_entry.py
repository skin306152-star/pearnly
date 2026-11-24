"""云端 Express 的用户入口什么时候出现（多产品共用：CC 设置「连接」里的「云端 Express」一行、CC 公司选择器与 ERP「从连接添加」的
「云端 Express」标签）。

11-24 第一次真机首测：连接通了，但云端那份 CC 没有成员（D3）、受限权限下找不到账套（D4），端到端走不通。功能没走通之前，正式部署
（``PEARNLY_DEPLOYMENT`` 是 ``production``，生产 compose 的默认值）一律不显示这些入口，免得客户看到点了不能用的东西。不是按租户开关：
同一个部署里所有租户一样。后端接口与 CC 桌面版里的云端代码原样保留。

非生产部署照常显示，验证不受影响：测试机测试环境（``infrastructure/testenv/run.sh`` 设 ``testenv``）、回归实验室（``dev``）、
本机验收栈（``acceptance``）。

**什么时候拿掉**：云端 Express 第二阶段（D3 / D4 等，评估见 docs/work/2026-11-24-express-cloud-phase2.md）真机 6 段走通后，删掉本模块、
``www/assistant.py`` 与 ``hooks.py`` 里的调用、前端三处 ``pearnly_cloud_express`` 判断（settings.tsx、company-picker.tsx、
company-connect.js），入口照常渲染。
"""
from __future__ import annotations

import os

DEPLOYMENT_ENV = "PEARNLY_DEPLOYMENT"
PRODUCTION = "production"


def visible(environ=None) -> bool:
    """这个部署里显不显示云端 Express 的入口：非生产才显示。没设部署名按生产算（生产 compose 的默认值）。"""
    env = os.environ if environ is None else environ
    return str(env.get(DEPLOYMENT_ENV) or PRODUCTION).strip().lower() != PRODUCTION


def extend_bootinfo(bootinfo) -> None:
    """Desk 启动信息带上 ``pearnly_cloud_express``（ERP「从连接添加」据此决定摆不摆「云端 Express」标签）。每次启动都算，不进按用户缓存。"""
    bootinfo.pearnly_cloud_express = visible()
