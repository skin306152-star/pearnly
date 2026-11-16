# 云端 Express：云端那份 CC 的安装命令（2026-11-16）

## 那一行命令
用户在云端主机开始菜单打开 PowerShell，打（令牌是桥上显示的那个，下面是假的）：

```
powershell -NoProfile -ExecutionPolicy Bypass -File \\tsclient\Z\cc\cloud_install.ps1 -Token 7K3M-Q9TB-ABCD-EFGH-JKMN-PQRS-TVXX
```

`Z` 是桥映射进云端会话的隧道盘，以桥窗口显示的盘符为准。

## 文件从哪里来
桥（客户自己的 CC 电脑）把三份文件放进隧道盘的 `cc\`，代码在 `companion/src/companion/cloud_link/cloud_package.py`：

- `cloud_install.ps1`：源码在 `companion/packaging/cloud_install.ps1`，正式包里作为数据文件放在 `cloud_link\` 旁边（`companion.spec` 的 datas）。
- `CC-Cloud.zip`：发布流水线（`.github/workflows/companion-release.yml`）现在跟正式安装包同一个提交一起打（`release.ps1 -BuildOnly -Cloud`），
  经 `infrastructure/production/receive-companion.py` 放在安装包旁边（`releases/<版本>/<提交>/CC-Cloud.zip`，不走 R2，从服务器本机给），
  自动更新用的那份 `latest.json` 多一项 `cloud: {version, url, sha256, size}`。发布时也从公网读回比对 sha256。**这次只改了脚本与清单格式，没有执行真实发布。**
- `cloud.json`：发布的 `{version, sha256, size}` 加上 `host`，值是 `rdp.host(连接主机)`，也就是桥配对用的那个主机地址。

桥本机缓存在 `%LOCALAPPDATA%\Pearnly\cloud-package\<版本>\`。缓存没有或 sha256 不对，就按 `latest.json` 的 `cloud` 下载（经 `net.client`，跟随重定向），
下完核大小和 sha256，不对就删掉，报 `checksum`；离线时用缓存里校验得过的最新一版。写进 `cc\` 时每份先写 `.part` 再改名，已存在且 sha256 相同就跳过，`cloud.json` 最后写。

什么时候做：设置页点「配对」（`cloud_pair_begin`）时先在后台备好 `cc\` 再生成令牌，回包另带 `drive`（隧道盘符）和 `command`（那一行命令，令牌已填好），
令牌和命令不进日志、不写文件；准备失败回 `{ok:false, code}`（`no_package` / `download_failed` / `checksum` / `write_failed`），`cloud_status` 另带 `package` 同一个码。
点「连接」起宿主后，只有 `cc\cloud.json` 已经在时才看一次清单，云端包版本变了才重新放；`cc\` 是空的不联网。空闲时什么都不做。前端显示这部分不在本次范围。

## 脚本做什么
1. 每次都把 `CC-Cloud.zip` 拷到 `%LOCALAPPDATA%\Pearnly\CC-Cloud\`，算 SHA-256，要和 `cloud.json` 的 `sha256` 一致；不一致就打一行结果、以 11 退出：不解压、不运行、不建快捷方式。
2. 解压到 `%LOCALAPPDATA%\Pearnly\CC-Cloud\<版本>`（已有就不重解压）。
3. 运行一次 `companion.exe --cloud --pair <令牌>`，按退出码打印一行结果：0 配对完成、3 未找到隧道盘、4 缺少主机信息、5 令牌有误、6 配对超时、7 配对被拒绝。
4. 配对成功才在本用户的「启动」文件夹建 `CC.lnk`（参数只有 `--cloud`），再启动一次 `companion.exe --cloud`。配对失败（含超时）不留快捷方式：之前安装建的、指向上面目录的 `CC.lnk` 删掉，别的不碰。

## 校验值在哪里看
`cloud.json` 里的 `sha256`，也就是 `release.ps1 -Cloud` 构建日志里 `Cloud package:` 那一行打印的值。桥放进 `cc\cloud.json` 的也是这个值。

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
