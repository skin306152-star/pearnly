# 点一下就开通：从填表到租户能登录

日期：2026-09-18。状态：**已实现并实测**（13 项断言全过，本机 **27 秒**走完）。

## 用户要的

> "要不要我把它接成点一下就开通？" → **"做"**

在这之前，"开一张单"其实分成三件事，而且**中间断了两处**：

| 环节 | 之前的真实状态 |
|---|---|
| Desk 上填开户单、保存 | ✅ 能填能存 —— 但**只是存了一条记录**，`status` 是只读的 `draft`，**没有任何东西会去建账号** |
| 把单子变成账号 + 租户 | ❌ 只有 API 那条路（`api_issue`）会做；Desk 表单根本没有这个动作 |
| 建站、装 App、发权限、建工作台 | ⚠️ 要人在宿主机上敲 `scripts/sample-provision.sh run <单号>` |

所以"正常开户流程"当时是走不通的：表单存了草稿，然后就没有然后了。

## 现在是什么样

```text
/app/pearnly-issue/new
  填 公司名称 · 邮箱 · 用户名 · 初始密码
  选产品 + 填域名前缀（勾了哪个产品就开通它全部功能）
       ↓  点一下「发号并开通」
  发号：建身份账号 + 建/找租户          （控制面，同步，1 秒）
       ↓  自动排入开通队列
  常驻执行器接手：建站 → 装 App → 配统一登录 → 重启 → 收尾
                 → 建租户账号 → 发功能权限 → 建工作台 → 标记完成
       ↓  表单上的状态灯实时走：开通中 3/11 → … → 已开通 11 步
  租户用自己的域名登录，用用户名 + 初始密码进得去
```

实测：**点一下，27 秒后租户就能登录**（11 个步骤，其中 `set_line` 记为 skipped，
因为这个租户还没配 LINE 渠道 —— 明确记「不用做」，不含糊成「做完了」）。

## 三块东西

### 1. 表单上补上"发号"这个动作

`pearnly_control.issues.issue_from_doc(issue)` + `_activate(doc, operator)`：

- 原来 `issue()` 把"建单 + 建账号 + 建租户"写成一整条；现在拆出 `_activate()`，
  **API 一次发号和 Desk 存草稿后点发号共用同一份** —— 分开写迟早有一条路忘了改。
- 幂等：已经 `issued` 的单子重复点不会重复建账号。

### 2. 常驻执行器（`scripts/provision-runner.sh`）

建站要在别的容器里跑 `bench`，而**控制面容器里没有 docker**（P1.05：
部署代理独立、运行进程不持 root）。所以控制面只能"记下要做什么"，
真正干活的是宿主机上的这个进程，它每 3 秒问一次"有没有待办"。

用 **launchd**（`~/Library/LaunchAgents/com.pearnly.provision-runner.plist`）
而不是 `nohup &`：nohup 挡得住挂断信号，挡不住"父进程被杀"和"重开机" ——
实测 `nohup` 起来的进程会随启动它的会话一起消失。launchd 崩了自动拉起、
开机自动运行，而且 `launchctl` 能查到真实状态，不用靠 pid 文件猜。

```bash
scripts/provision-runner.sh install    # 装成常驻（一次就够）
scripts/provision-runner.sh status     # 在不在跑
scripts/provision-runner.sh log        # 跟日志
```

### 3. 表单上的状态灯与进度

`pearnly_issue.js`：状态灯（未发号 / 开通中 n/N / 已开通 / 失败）+ 进度弹窗
（每一步中文化：建站点、装产品 App、发功能权限…），跑完自动刷新。

## 踩到的三个坑（都会复发）

1. **Desk 的 Password 字段读属性拿到的是密文。**
   `doc.account_password` 是 `__Auth` 里那段密文，不是明文。拿它去身份服务建号，
   结果是"账号建出来了、就是登不进去"。要用 `doc.get_password(...)`。
   API 那条路因为密码来自函数参数，一直是好的 —— 两边共用 `_activate` 之后才暴露。

2. **改了 doctype 的 `.js`，`clear-cache` 有时不够。**
   表单的客户端脚本缓在 DocType meta 上（`__js`）。只跑 `clear-cache`，
   浏览器拿到的还是旧脚本，表现是"新加的按钮不出现"。
   要再补一次 `bench --site X execute frappe.clear_cache`。
   已写进 `scripts/dev-reload.sh`（清缓存那一步现在两件事都做）。

3. **`dev-reload.sh --no-cache` 是"不清缓存"。**
   字面看着像"不用缓存"，实际是**跳过**清缓存。改完 hooks/doctype 别带这个参数。

## 可复现的验收

```bash
scripts/provision-runner.sh install        # 前提：执行器在跑
node scripts/sample-verify-onboarding-ui.mjs dms
```

它真的开浏览器走这条路：填表 → 点「发号并开通」→ 等状态灯变「已开通」
→ 拿新租户的账号登进新站点 → 确认拿到的功能不是空的。

## 边界（如实说）

- **LINE 真机**仍然没验：`set_line` 那步在没有渠道时记 skipped，接口和按租户隔离都在，
  但要真通道 + 公网 HTTPS 回调才能验。
- 执行器只在本机（launchd）。将来上服务器要按 P1.05 做成那边的部署代理。
- 独立发号页（`/pearnly-issue`）**还没有**这两个按钮，只在 Desk 表单上有。
