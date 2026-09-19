# 云端开户入口缺执行权限

用户报告 ISS-2026-0239 无法开户，并要求暂缓部署。代码基线 4c2fbc8；本次只读诊断服务器、修复仓库防复发，尚未修改服务器权限或重试工单。

## 已证实

- 发号单 issued，目标 production-contabo；PR-2026-0241 的 DEP-2026-0242:new_site 失败，输出为 Cloud provisioning connection failed；部署 testdms.pearnly.com 为 pending。
- 普通 operator SSH 正常；开户专用受限 SSH 发送无效任务 `{}`，未进入业务处理，直接返回 `/opt/pearnly/deploy/infrastructure/production/provision-agent.py: Permission denied`。
- 云端脚本权限 664，仓库 Git mode 100644；authorized_keys 的固定命令直接执行该脚本，需要执行位。云端部署源码标记为 26158211ee4a3c6745537b10d08812eb70804f8c。
- 云端 DMS sites 目录没有租户 Site；testdms 的旧 manifest 属于 DEP-2026-0226 且 deleted=true。本次失败发生在脚本启动前，不能把旧 manifest 当作本次已建站证据。

## 修复与验证

- provision-agent.py 的 Git mode 改为 100755，不改变其业务逻辑。
- 回归直接以文件路径执行固定入口，发送无效任务并验证返回受控 JSON；验证在 STATE.mkdir、锁、Docker 及建站逻辑前拒绝。
- 新测试在修复前准确失败，修复后连同原有域名、归属、App 白名单、路由测试共 4 项通过；架构检查和 diff 检查通过。
- Development checks 加入该入口测试，避免以后源码打包丢执行位而测试仍绿。

## 待确认的运行操作

仅把云端现有同一路径 chmod 755，不替换源码或镜像；先重复无效任务协议探测。成功后可使用原生 retry_failed_steps 重试 PR-2026-0241，并跟踪完整开通结果，不重发账号。

用户明确暂缓部署，且根 AGENTS.md 要求外部系统写入明确授权，因此服务器权限修复及真实建站重试待确认。任何后续失败继续基于实际步骤日志处理，不绕过权限、幂等、归属和计费约束。
