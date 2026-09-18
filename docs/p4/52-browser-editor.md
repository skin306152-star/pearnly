# 52 · LINE 内编辑器（订车 / 客户档两种模式）

线上基准：`501589f0`（老产品 `pearnly-app`）

## 这一批做了什么

LINE 里点预览卡的 `[แก้ไข]`（编辑）→ 打开**本站点网页编辑器** → 改完保存 → 新版预览卡
发回 LINE。老产品那一屏（`static/dms-booking-edit/`）现在整套搬完了：

| 搬的东西 | 落点 | 对照的线上文件 |
|---|---|---|
| 一次登录取齐的权威快照 | `pearnly_dms/dms_edit_snapshot.py` | `services/erp/dms_edit_snapshot.py` |
| 订车草稿编辑器（load/paints/geo/save） | `pearnly_dms/dms_booking_edit.py` | `services/line_dms/booking_edit.py` |
| 客户档编辑器（load/geo/save） | `pearnly_dms/dms_customer_edit.py` | `services/line_dms/customer_edit.py` |
| 接口面 | `pearnly_dms/dms_browser_api.py` | `routes/line_dms_booking_edit_routes.py` |
| LIFF 落地页 → 交给前端 | `pearnly_dms/www/liff/dms-booking.html` | `static/dist/dms-booking-edit.html` |
| 页面本身 | `frontend/src/pages/dms/EditorPage.vue` | `static/dms-booking-edit/dms-booking-edit.js` |
| 卡片上的编辑按钮改成深链 | `pearnly_dms/dms_cards.py` `_edit_button` | `services/line_dms/cards.py` `_edit_button` |
| 点编辑 → 回一个"打开编辑页"按钮 | `pearnly_dms/dms_flow.py` `_open_browser_editor` | `services/line_dms/customer_edit_entry.py` |
| 四语文案（82 条 × 4 语） | `pearnly_dms/translations/*.csv`（`dms.edit.*`） | `static/dms-booking-edit/dms-booking-i18n.js` |

顺手**删掉**了线上早已退役的"LINE 里逐字段改"那一套
（`edit_menu_message` / `edit_prompt` / `EDIT_FIELDS` / `ACT_EDIT_FIELD` /
`ACT_EDIT_CANCEL`，以及语言包里对应的 11 条键）—— 线上
`customer_edit_entry.py` 写明 "retired text-edit events cannot change customer fields"。

## 三处**有意**与老产品不同的地方

1. **凭据不是站点会话，是一张一次性票据**（`dms_browser_ticket.py`）。
   老产品换的是一个只能编辑本次草稿的 JWT，新平台照这个意思做：票据只绑
   LINE 用户 + 编辑模式，**不绑 nonce**（nonce 每次保存都轮换，绑了反而会在用户改两遍后失效）。
   解绑 / 会话过期立刻失效：每个请求都重新查一次绑定与会话，票据本身不缓存任何权限。

2. **取票有两条路**：LINE 内走 LIFF `id_token` 验签（正式路径）；没有 LIFF 时走
   "本站点已登录会话 → 这个账号名下的生效绑定"（本机验收、以及没配 LIFF 的站点回落）。
   第二条没有放宽任何权限 —— 拿到的仍然只是**他自己**那份草稿。

3. **本机样例站点开了"本地镜像"**（`pearnly_line_mirror`，`pearnly_core.line._mirror`）。
   本机没有 LINE 通道，而保存的语义是"卡片送达才算成功"，不镜像的话本机点保存永远失败。
   镜像把消息原文记进推送日志（`trigger=line_mirror`），**两道锁**保证它进不了生产：
   站点要有这个开关，**并且**这个站点一根 LINE 凭据都没配。

## 怎么验收

```bash
# 接口 / 守卫 / 票据（91 项，站点逻辑 + HTTP 路由）
scripts/sample-verify-dms-edit-browser.sh dms one761900.localhost 8103

# 真开页面点一遍（订车 + 客户档两种模式：Playwright 点击 + 服务端回读）
PEARNLY_NODE=<能跑 mjs 的 node> scripts/sample-verify-dms-edit-page.sh dms one761900.localhost 8103
```

第二条会先用**真实 DMS 主档**造一份草稿（车型/颜色/府县区邮编/称谓全取自真主档），
再把票据塞进 `sessionStorage` 打开页面 —— 与 `/liff/dms-booking` 那一页交接的做法完全一致。
截图落在 `docs/p4/evidence/dms-editor-*.png`。

## 环境侧的两处修正（不是代码问题，但影响能不能验）

1. **站点的 DMS 端点之前是个占位账号**（`EP-00001`，用户名 `verify`，真实 DMS 拒登）。
   已按仓库既有流程用老产品导出的 `dmstest` 建了 `EP-00050`（
   `scripts/sample-seed-dms-endpoint.sh`），并把那个占位端点**停用**（留记录、不删）。
   ⚠️ 老产品文档写明 `dmstest` **只允许一个活跃登录**：连着跑两轮验收偶尔会撞上
   "并发登录"，表现是这一页如实报"主档暂时读不到、请稍后再试"（可重试，不是 bug）。

2. **本机的 npm shim 是坏的**：`~/.local/bin/npm` 指向
   `/Applications/ChatGPT.app/.../npm-cli.js`，那个路径已经不存在。前端构建要么修掉这个
   shim，要么用别的 node（本次用官方 node v22 解压到 `/tmp` 跑的 `npm run build`）。
   与代码无关，但 `cd frontend && npm run build` 这条日常命令现在是跑不起来的。

## 还没做的（明确留的口子）

- **真机 LINE 未验**：LIFF `id_token` 那条路要真机才能跑（用户已定"真机不急，接口留好"）。
  接口、验签、票据、回落路径都在，`browser_config` 会如实说本机 `available: false`。
- **凭证编辑（`?credentials=dms`）没做**：那是"改 DMS 账号密码"，与这两个编辑器不是一回事，
  老产品里也是单独一个页面（`dms-credentials.js`）。
- **差异卡的编辑按钮仍是 postback**（老产品如此）：用户点它时由
  `dms_flow._open_browser_editor` 回一个深链按钮。客户档其它卡片上的编辑按钮直接就是深链。
