# -*- coding: utf-8 -*-
"""云端 Express：CC 桌面版经客户自己那台电脑（桥）连云端主机上的 Express（评估 docs/work/2026-11-11-express-cloud-assessment.md
第 4 节，施工短版 2026-11-15-express-cloud-build-assessment.md）。

云端主机不能上网：云端那份 CC 的 HTTP 请求写进映射盘里的隧道文件夹，由桥上的 CC 代发到配对时记下的站点，回复写回同一个文件夹。

范围：本包现在只有「文件隧道」本身（步 C），现有代码还没有任何地方导入它，没连云端的电脑不受影响。

- ``tunnel_protocol``：请求 / 回复文件的格式、签名、路径与请求头白名单、防重放（纯函数，不做 IO）；
- ``tunnel_transport``：云端那一侧，``httpx.BaseTransport`` 子类；下一步由统一建客户端的入口（``net.client``）在 ``--cloud``
  模式下装上，CC 现有的各个客户端照旧用 httpx；
- ``tunnel_relay``：桥那一侧，处理一个请求文件、代发到站点、写回、清理旧文件；下一步由 ``--cloud-link`` 子进程调用。
  处理逻辑与监视方式分开：现在用轮询（``poll_once``），接进子进程时换成 ``ReadDirectoryChangesW``，只换「什么时候叫 ``process``」。

会话管理、云端 CC 的部署、配对代登记不在这里（后续步骤）。
"""
