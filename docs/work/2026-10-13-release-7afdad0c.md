# 上线 7afdad0c：装了 Express 的电脑新建 Express 连接、CC 界面语言跟随设备（2026-10-13，曼谷 14:18–14:27）

main 7afdad0c，镜像 `ghcr.io/skin306152-star/pearnly/frappe@sha256:cbeecd72e3d98d9da17c83e50e5bc9edf4286c5f528fb980f22e2c3126476759`
（构建 run 38361094257；stage run 38361783229）。日志 `/opt/pearnly/state/deploy-7afdad0c.log`。教程录制中发现、教程窗口转达用户要求「修好就部署」。

## 内容

- 装了 Express 的电脑登录 CC 桌面版只连 Express 连接，本站还没有就新建（`companion/credentials.py` `_device_connection`）；
  原来找不到 Express 就退到已有的 MR.ERP 连接，testcowork 清空后只剩 MR.ERP 时新笔记本连成了 MR.ERP。没装 Express 的照旧先连 MR.ERP。
- CC 界面语言：这台设备上手动选的 > 这台设备第一次登录时登录页显示的语言（点选或按系统语言自动显示都算，同时存为账号语言）>
  这台设备记住的 > 账号语言 > 系统语言 > 英文。对话页请求带上页面语言（`X-Pearnly-Lang`），服务端文字、工具步骤、CC 回复语言一致；
  规则在 `pearnly_core/account/ui_language.py`。CC 桌面版把 Windows 显示语言交给网页的改动随下一版桌面版发。

## 关

- 本地 `development.yml` 68 步通过；分支 Development checks（38360472137）、Companion checks（38360472016）通过。
- 改了 `pearnly_core`：测试机镜像 f9bf6551（与线上 dff7cf63 同一底座，第二批未改依赖）挂候选源码跑 `ci-candidate-checks.sh offline`，71 组 EXIT 0；
  另跑 `pearnly_core.account.test_preferences` 18 条，17 过，1 条 `test_credentials_are_scoped_to_current_session` 改动前就失败。
- 回退目标 `…@sha256:dff7cf63…dc1f`（c0d1f941）。

## 部署与回读（UTC）

- 激活：ERP 07:18、Cowork 07:21、DMS 07:24、基础层 07:26–07:27。
- 生产容器全部新镜像；三个识别进程 NOFORK=1；六站 maintenance=0、部署后 Error Log 0；mariadb 3.56 / 8 GiB；六个产品域名 ping 200，fatcc.com 首页 200。
- 线上代码核对：`hooks.py` 注册了 `use_page_language`，`credentials.py:247` 为 `order = ('express',) if express else ('mrerp', 'express')`，`account/ui_language.py` 存在。
- CC 桌面版不重发（1.5.0 在泰文 Windows 上已按泰文发请求，两项修复都在服务端）。

## 待复核

- 教程窗口在泰文笔记本上重装重录：登录页 ไทย 不点，登录后整页泰文；设置页连接为 Express。
