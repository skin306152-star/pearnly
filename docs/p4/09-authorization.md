# P4.04 授权管理：让"勾选的功能"真正生效

日期：2026-09-18。状态：**功能门禁已生效并实测**。

## 之前的问题

租户开单上勾了功能，但那只**记录在单子上**，没有任何约束力。更糟的是租户管理员
拿到的是 `System Manager` —— Frappe 的**框架超管角色**（能改系统设置、装应用），
同时又没有任何功能级限制：**没买的功能他照样能访问**。

## 改法：功能 → 角色 → DocType 权限

```
勾了功能            →  建对应角色          →  给该角色 DocType 读写权限
cowork:intake           Cowork 录入             Intake Record
erp:purchase            ERP 采购                Purchase Document / Purchase Line
dms:id_card_ocr         DMS 身份证录入          DMS ID Card Intake
dms:customer_match      DMS 客户匹配            DMS Customer Link
```

**没勾的功能 → 角色不发 → 对应 DocType 访问不了。** 这是"未购买功能不可访问"
能成立的地方。

### 租户管理员的角色被换成三样

| 角色 | 作用 |
|---|---|
| `Desk User` | 能进 Desk |
| `<产品> 管理员` | 能管本产品的员工账号（User / Role / User Permission / Contact），**碰不到系统设置** |
| 买到的功能角色 | 每个已购功能一个 |

`System Manager` **被拿走** —— 契约要求"租户管理员不能授予平台权限或未购买 feature"。

只有**有实际可挡对象**的功能才建角色。像"首页（订阅与计费）"这种目前没有对应
DocType 的，就不在权限表里假装有 —— 等产品界面做出来再补（不装样子）。

## 顺带解决：拿掉超管后 Desk 会空

实测踩到：拿走 `System Manager` 后，Frappe 自带那些工作区对租户不可见，
**打开 Desk 是一片空白**。

所以开通时给租户建一个"只列他买了什么"的工作区（公开，但可见性限定在本租户角色上），
并在桌面给它一个图标。产品自己的界面还没做，这个是过渡落点，同时也是
"我买了哪些功能"的可见答案。

## 验证证据

脚本 `scripts/sample-verify-feature-gate.mjs`（真实浏览器登录后实测）：

```
[1] 买了的「DMS ID Card Intake」      → HTTP 200        ✓ 可访问
[2] 没买的「DMS Customer Link」       → HTTP 403        ✓ 被拒
        Insufficient Permission for DMS Customer Link
```

该租户角色的实测值：

```
["Desk User", "DMS 管理员", "DMS 身份证录入"]      ← 没有 System Manager
```

界面实测：租户打开 Desk 有图标、无 JS 报错，图标是该产品的图形。

## 踩到的坑（都会复发，逐条记）

1. **`bench execute` 对 `--kwargs` 做的是 Python `eval`**
   （`frappe/commands/utils.py:286`）。传嵌套 dict 一旦出问题，它会把**真实异常吞掉**
   再换成一句 `NameError: name 'pearnly_core' is not defined` —— 照着假错误查会白查很久。
   解法：参数用 **JSON 字符串**传，绕开 eval 语义（`ensure_feature_roles(spec_json)`）。

2. **`frappe.cache.delete(key)` 对 hash 无效且不报错。**
   `desktop_icons` / `bootinfo` 是 hash，`delete` 返回 0 但键原样留着 ——
   **silent no-op**，靠 try/except 兜不住。必须用 `delete_key`。

3. **`clear_desktop_icons_cache()` 不带参数只清当前会话用户。**
   开通是 bench 以 Administrator 跑的，清的是 Administrator 的缓存；
   租户用户那份"什么都没有"的旧缓存会一直留着。开通这种低频动作上整体清最稳。

4. **工作区的 `module` 必须是该产品自己的模块。**
   Frappe 判可见性时查 `workspace.module in user.allowed_modules`，租户用户被允许的
   只有他所用产品 App 的模块（实测有 `Pearnly DMS`，没有 `Pearnly Core`）。
   写成核心层模块会被判无权限直接丢掉 —— 表现为"数据都建好了，桌面还是空的"。

5. **步骤的 payload 是规划时算好存库的。** 只补新步骤不够：改了生成逻辑（比如工作区
   要带产品模块名）对已存在的步骤永远不生效。现在重跑 plan 会**刷新 pending/failed
   步骤的参数**（`done` 是既成事实不动，改了就是骗人）。

6. **`next_steps` 曾经按请求状态过滤。** 步骤被重置回 pending 后，只要请求状态还停在
   `done`，就永远没人执行它（表现是"没有待办步骤了"，实际有）。
   改成**按步骤驱动**：步骤是事实，请求状态是它的投影。

7. **桌面图标的 `link_to` 是动态链接。** 类型为 Workspace Sidebar 时必须指向真实存在的
   **Workspace Sidebar** 记录（不是 Workspace），否则报
   `LinkValidationError: Could not find Link To: <标题>`。

8. **中文标签的桌面图标会画成空白方块**（图标按标签找 SVG，找不到就画标签首字母，
   而字母表里没有汉字）。租户图标还会用**租户名**当标签，所以显式给 `logo_url`。

## 新增的运维能力

- `scripts/sample-provision.sh retry <发号单号>`：把失败步骤重置后重跑。
  执行器遇到失败会**停住不跳步**（后面的步骤依赖它），所以修好原因后必须能重试，
  否则一次失败就把整张单卡死。

## 尚未做（不掩饰）

1. **产品界面还没做**：租户现在看到的是"工作区 + DocType 列表"，不是产品自己的界面。
   下一步是前端（见 ADR-005）。
2. **功能角色只覆盖到 DocType 层**：像"首页（订阅与计费）"这种没有 DocType 的功能，
   目前无从约束。
3. **没做前端菜单**：前端要根据后端返回的授权渲染菜单，那部分等前端工程建起来。
4. **账套（工作区/Book）级范围还没做**：现在只做到"产品功能"这一层，
   计划里的 `member_scopes`（账套级授权）还没落地。
