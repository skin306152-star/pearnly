# -*- coding: utf-8 -*-
"""云端 Express：CC 桌面版经客户自己那台电脑（桥）连云端主机上的 Express（评估 docs/work/2026-11-11-express-cloud-assessment.md
第 4 节，施工短版 2026-11-15-express-cloud-build-assessment.md）。

云端主机不能上网：云端那份 CC 的 HTTP 请求写进映射盘里的隧道文件夹，由桥上的 CC 代发到配对时记下的站点，回复写回同一个文件夹。

范围：文件隧道（步 C）、桥本机的连接配置与设置页动作（步 E、G）、连接窗口（``window`` / ``session`` / ``nonscriptable``），
以及把它们串起来的桥侧宿主 ``host``。``tomo_bridge`` 只在对话页调到云端动作时才导入本包，没连云端的电脑不受影响。

- ``tunnel_protocol``：请求 / 回复文件的格式、签名、路径与请求头白名单、防重放（纯函数，不做 IO）；
- ``tunnel_transport``：云端那一侧，``httpx.BaseTransport`` 子类；下一步由统一建客户端的入口（``net.client``）在 ``--cloud``
  模式下装上，CC 现有的各个客户端照旧用 httpx；
- ``tunnel_relay``：桥那一侧，处理一个请求文件、代发到站点、写回、清理旧文件；由 ``host`` 在桥进程里的后台线程轮询调用。
- ``host``：桥侧云端连接宿主（隧道盘 subst、``ctl\\`` 会话交接、中转线程、连接窗口），设置页「连接」时起、「断开」与关窗时停。

- ``link_store``：桥本机的云端连接配置（主机、端口、用户名、域、云端设备密钥与各站点令牌的 DPAPI 密文；密码在凭据管理器），
  配对代登记由对话页经 ``tomo_bridge`` 的 ``cloud_pending`` / ``cloud_token`` 做（步 G）。

云端 CC 的部署不在这里（后续步骤）。
"""
