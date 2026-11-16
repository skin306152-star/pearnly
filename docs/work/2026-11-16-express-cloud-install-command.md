# 云端 Express：云端那份 CC 的安装命令（2026-11-16）

## 那一行命令
用户在云端主机开始菜单打开 PowerShell，打（令牌是桥上显示的那个，下面是假的）：

```
powershell -NoProfile -ExecutionPolicy Bypass -File \\tsclient\Z\cc\cloud_install.ps1 -Token 7K3M-Q9TB-ABCD-EFGH-JKMN-PQRS-TVXX
```

`Z` 是桥映射进云端会话的隧道盘，以桥窗口显示的盘符为准。

## 文件从哪里来
桥（客户自己的 CC 电脑）把三份文件放进隧道盘的 `cc\`：`cloud_install.ps1`（`companion/packaging/cloud_install.ps1`）、`CC-Cloud.zip` 和 `cloud.json`（`release.ps1 -BuildOnly -Cloud` 打出来）。放置时桥要往 `cloud.json` 里加一项 `host`，值是 `rdp.host(连接主机)`，也就是桥配对用的那个主机地址。**桥放置这一步还没做。**

## 脚本做什么
1. 把 `CC-Cloud.zip` 拷到 `%LOCALAPPDATA%\Pearnly\CC-Cloud\`，算 SHA-256，要和 `cloud.json` 的 `sha256` 一致，不一致就停。
2. 解压到 `%LOCALAPPDATA%\Pearnly\CC-Cloud\<版本>`。
3. 在本用户的「启动」文件夹建 `CC.lnk`，参数只有 `--cloud`。
4. 运行一次 `companion.exe --cloud --pair <令牌>`，按退出码打印一行结果：0 配对完成、3 未找到隧道盘、4 缺少主机信息、5 令牌有误、6 配对超时、7 配对被拒绝。配对成功后再启动一次 `companion.exe --cloud`。

## 校验值在哪里看
`cloud.json` 里的 `sha256`，也就是 `release.ps1 -Cloud` 构建日志里 `Cloud package:` 那一行打印的值。桥窗口显示校验值要等桥放置那一步做完。

## 不做什么
不需要管理员权限；不改系统设置和执行策略（`Bypass` 只对这一个 powershell 进程有效）；不建计划任务、不写注册表；上面两个目录以外的地方都不碰。令牌不写进文件、快捷方式和日志，也不回显。

## 主机名约定（两侧一致）
配对的认证密钥按主机名派生。桥用 `rdp.host(连接主机)`；云端从 `cc\cloud.json` 的 `host` 读，再按同一规则规范化（去空白、去末尾的点、转小写）。配对成功后，设备密钥存在云端凭据管理器的 `Pearnly CC/cloud-device/<主机>`；以后要用主机名时，从这条凭据的名字里取。

## 连带回归（`scripts/check-impact.py`，本次云端侧接线命中 `cc-operator-target`）
| 入口 | 结果 |
|---|---|
| `cc.desktop.operator` | 离线通过（companion 全套）；实机没测到（需 Windows） |
| `cc.desktop.operator.mrerp` | 离线通过；实机没测到（需 Windows） |
| `cc.desktop.write` | 离线通过；实机没测到（需 Windows） |
| `cc.desktop.places` | 没测到（需 Windows） |
| `cowork.cc.channel` | 没测到（需 Windows） |
| `erp.cc.connect` | 没测到（需 Windows） |

本地模式代录取凭据、`net` 不装钩子由 `companion/tests/test_cloud_side.py` 守住。
