# 教程录制中登记的问题（待用户通知额度恢复后施工）

用户 2026-10-13：教程报来的问题只登记，等通知再施工。阻塞的排前面。

## 1. CC 桌面版自动更新被 Windows「智能应用控制」拦截（非阻塞）

- 报告：教程窗口，2026-10-13 18:18（曼谷）。
- 环境：录制笔记本，Windows 11 家庭版 25H2，智能应用控制为「强制」
  （`HKLM\SYSTEM\CurrentControlSet\Control\CI\Policy\VerifiedAndReputablePolicyState=1`）。
- 现象：1.5.1 发布后这台电脑一直停在 1.5.0；系统通知「Part of this app has been blocked … can't confirm who published
  CC-Setup_053n42mm.exe」；代码完整性日志有 3033、3077 事件。首次从官网手动安装时在 Edge 点「保留」能装上，后台静默升级被拦。
- 影响：开着智能应用控制的新 Win11 电脑（新装系统默认评估或开启）收不到任何自动更新，用户看不出原因。
- 约束：用户已定不买签名证书（记忆 no-code-signing-cert）。
- 初步方向（待评估）：桌面版检测到安装包被拦（启动安装后进程立即失败 / 代码完整性拒绝），在 CC 里提示手动下载新版并给链接；
  心跳带上「更新被系统拦截」状态，网页 / CC 设置页如实显示。
