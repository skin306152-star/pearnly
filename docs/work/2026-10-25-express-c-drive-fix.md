# 测试机 C 盘 Express 开单据报 -330 ISRUN1：根因是 Express 授权（2026-10-25）

主会话 10-25 派的专查任务：测试机（lenovo-office-lan）`C:\PearnlyExpress\70EXP` 这套 Express 能登录、能选公司，
一打开单据（例如 2-4-2 赊销单）就连续弹「Error No :-330 (92401) Tag Name not Found ISRUN1」（之后 ISRUN2~4），
然后自己退出；同一套程序从 S 盘 / accserver 运行正常。目标是查清根因、给出可靠修法。

## 结论

**不是账套数据、索引、secure 配置或运行环境的问题，而是 Express 自带的防拷贝授权。**
程序目录的 `Serial.TXT`（界面显示 S/N W-10T-260123）签给 accserver 那套安装。从 C 盘本机运行时，授权核对不通过，
每个单据窗口打开数据表时都不会打开同名的 .CDX 索引，于是 ISRUN1~4 全部报「找不到」，Express 随即退出。
登录、选公司走的是主连接，不受影响，所以表现为「能进公司、一开单据就错」。

这个问题**不能靠改 C 盘的数据或配置修好**。改程序、伪造卷序列号或服务器名、仿冒网络路径都属于绕过软件保护，不做
（主会话 10-25 也明确禁止）。正当的修法由用户决定，见「修法」一节。

## 证据

### 1. 实机跟踪（10-25 16:36–16:39，T9GIZ）

用 Windows 自带的内核文件跟踪（`logman` 的 NT Kernel Logger，加 `tracerpt` 导出），以 skin3 普通权限启动
`C:\PearnlyExpress\70EXP\ExpressI.exe`。用户本人登录后，打开 T9GIZ，再按 2-4-2 开赊销单。ExpressI 进程（pid 9160）相关的文件打开记录如下：

| 时间 | 动作 | ISRUN 文件打开 |
|---|---|---|
| 16:37:32.448 | 打开公司 | `ISRUN.DBF` ×2 成功 → **`ISRUN.CDX` ×2 成功** |
| 16:37:35.438 | 工作日期确定 | `ISRUN.DBF` → **`ISRUN.CDX` 成功**（两轮） |
| 16:37:59.188 | 开赊销单（单据窗口） | `ISRUN.DBF` ×2 成功 → **没有打开 `ISRUN.CDX`** → 弹 -330 |

所有打开的结果码都是 0：文件存在，不缺权限，也没被别的程序占用。单据窗口根本没去打开索引，而不是打开失败。

### 2. 程序代码（只读反汇编 ExpressI.exe 与 C4DLL.DLL）

- Express 静态链接 CodeBase（DBF/CDX 引擎，版本号 0x1901，与程序目录 C4DLL.DLL 一致）。CodeBase 连接（CODE4）的第 0 个字段是 `autoOpen`，含义是打开表时是否自动打开同名 .CDX。依据：C4DLL 导出的 `c4getAutoOpen` / `c4setAutoOpen` 读写的就是 `[CODE4+0]`。
- 每个单据窗口初始化自己的 CODE4（`0x4033e2` 调 code4initLow(…, 0x1901, 0x51c)）后，紧接着执行
  `mov eax,[0x5dbf50]; mov [esi+0x308],eax`，即把 `autoOpen` 设成全局 `0x5dbf50` 的值。整个程序只有授权解密函数（`0x4ddd2b`）写 `0x5dbf50`。
- 授权解密用到两样东西：
  - 「程序所在位置」的指纹。启动时按 `argv[0]` 的完整路径判断是否从网络运行：路径以 `\\` 开头，或盘符是网络盘（用 `WNetGetUniversalNameA` 换算）。网络运行时记下服务器名并取程序所在卷的序列号（`GetVolumeInformationA`）；本机硬盘运行时改走本机硬盘信息（`\\.\X:` + `DeviceIoControl`）。网卡 MAC（`GetAdaptersInfo`）也参与。
  - 程序目录的 `Serial.TXT`。
- 结论：C 盘这份 `Serial.TXT` 与 accserver 那套逐字节相同，指纹却变成了本机硬盘，解不出正确值，单据窗口的 `autoOpen` 因此不对。实机跟踪里「单据窗口不开 CDX」正是这个结果。

### 3. 已排除

| 方向 | 结果 |
|---|---|
| 程序文件 | 验收窗口用 skin3 逐个算 md5：accserver 70EXP 与 C 盘 70EXP（程序根 + eng / thai / secure）1816 个文件内容全同 |
| 账套索引 | T9GIZ / T9NIEX12 / T9NIEXCP / T9HENG15 / 样板 DAT 的 ISRUN.CDX 都有 4 个标签（DOCTYP、PREFIX、DOCTYP+DOCCOD、DOCTYP+PREFIX），与 `70EXP\DBINF.DBF` 定义一致，与 S 盘原本同格式 |
| CC 写入 | T9GIZ 的 ISRUN.DBF / CDX 自 09-02 起没人动过，同样报错。之前「离线写没跑 cdx_reindex_runner 导致 -330」的判断不成立 |
| secure 配置 | C 盘 SCCOMP / SCACCLV 加过 T 账套行（T 行 COMPCOD 用账套名并补了权限行；原版全是 DATAT），10-20 CC 的重建程序重建过 SCCOMP 索引。登录、选公司都正常，与单据窗口不开索引无关；不需要换回 `secure\_orig` |
| 权限 / 占用 | Authenticated Users 有修改权，文件属性只有 A；跟踪里所有打开结果码为 0 |
| TEMP | 以 skin3 普通权限实测 TEMP = `C:\Users\skin3\AppData\Local\Temp`，可写。macadmin 读注册表时看到 codexops，是它按自己的 `%USERPROFILE%` 展开的 |
| `SECURE_PATH` / `DATA_PATH` | 不是环境变量，是报表表达式里的内部变量。secure 目录固定 = 程序目录 `\SECURE`，所以「程序从服务器跑、secure 和数据放 C 盘」也不可行（登录会写服务器上的 ISLOG） |

### 4. 旁证

测试机预读记录（`C:\Windows\Prefetch\EXPRESSI.EXE-*.pf`）：从 `\\ACCSERVER\D$\ACCOUNT\70EXP` 运行过 8 次（最后 10-20），从 69EXP 运行过 2 次，
从 `C:\PearnlyExpress\70EXP` 运行过 1 次（10-23）。没有找到 C 盘这套成功打开过单据的记录。

## 修法（需要用户决定）

1. **要完全不依赖 S 盘 / accserver**：向 Express 厂商（Express Software Group）为测试机这套本机安装申请授权（新的 `Serial.TXT`），
   或申请官方试用 / 演示版。这可能涉及费用或授权条款，需要用户出面。拿到后放进 `C:\PearnlyExpress\70EXP`（先备份旧的），
   再实测 T9GIZ、T9NIEXCP 开赊销单。
2. 在拿到授权之前：C 盘 T 账套仍可供 CC / ERP 读写和离线回归使用（这些不经过 Express 程序）。
   需要在 Express 界面里核对的，只能在有授权的安装上做，但那就会写局域网真账，现行规则不允许。
3. 不做：改 `Serial.TXT`、伪造卷序列号或硬盘信息、给程序打补丁、用网络路径或服务器名骗过授权检查。

## 实测状态

- 根因：实机复现 + 跟踪已确认（T9GIZ）。T9NIEXCP 是同一现象（验收窗口 10-25 15:4x 见过），没再单独跟踪。
- 修后实测：**未做**。在拿到合法授权之前没有可验证的修法。

## 现场与清理

- 只读诊断：没写 `C:\PearnlyExpress`、S 盘、accserver；没动 CC、实验栈和后台 S 盘 robocopy。
- 用户本人登录一次 Express，16:37:59 复现后错误框已点掉，Express 已退出。
- 跟踪文件 `C:\PearnlyStress\_xpfix`、`C:\Users\Public` 下的脚本、临时计划任务都已删除，NT Kernel Logger 已停止。
  验收窗口的 `C:\PearnlyStress\_acc_cc\xp-actions.json` 被我覆盖过（它每次运行都会重写），我截的 `xp-f*.png` 已删。
- 测试机占用表已登记 16:39 释放。

## 给开发窗口

根因不在 CC。之前推断「CC 离线写没跑 runner 导致 -330」不成立，CC 的写入代码不用为此修改。
仍建议保留「离线写真账套要用已安装 runner 重建 CDX」这条做法：它保证 Express 的索引和数据一致，与本问题无关。
