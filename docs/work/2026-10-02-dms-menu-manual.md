# DMS LINE 菜单第 6 项「คู่มือ」

分支 `codex/dms-menu-manual`（叠在 `codex/dms-recording-fixes` 上）。

## 内容

- 文字菜单第 6 行、两套图文菜单（有查询 3+3、无查询 3+2）都有「คู่มือ」，点开回三个视频按钮，只用回复令牌发视频。
- 泰文原图由 `scripts/draw-dms-line-menu-originals.py`（Sarabun Bold）画，英中日由 `scripts/render-dms-line-menus.py` 在 Frappe 镜像里出图。
- 菜单语言跟随用户 LINE 的语言（th / en / zh / ja，其他语言用泰文）。

## 视频地址（Cloudflare R2 `pearnly-media`，公开域名 media.pearnly.com）

2026-10-02 回读：6 个文件都返回 200，类型正确，支持 Range；视频 H.264 + AAC、1280×720，时长 225 / 111 / 180 秒，封面 1280×720、约 34 KB。

部署后在 DMS 站点配置写入（值用百分号编码后的完整 https 地址）：

| 配置键 | 视频 | 封面 |
|---|---|---|
| `pearnly_dms_manual_setup` | `DMS-1-ตั้งค่าเชื่อมต่อและพนักงาน.mp4` | `封面/6.0-ติดตั้งครั้งแรก.jpg` |
| `pearnly_dms_manual_customer` | `DMS-2-เมนู1-จัดทำข้อมูลลูกค้า.mp4` | `封面/6.1-จัดทำข้อมูลลูกค้า.jpg` |
| `pearnly_dms_manual_booking` | `DMS-3-เมนู2-จัดทำใบจอง.mp4` | `封面/6.2-จัดทำใบจอง.jpg` |

格式：`{"video": "https://media.pearnly.com/<编码后的文件名>", "preview": "https://media.pearnly.com/<编码后的封面路径>"}`。
编码：Python `urllib.parse.quote(路径)`（保留 `/`）。

## 上线步骤

1. 随下一批部署。
2. 写入上面三个配置键（先 testdms，用户手机验收后再写正式站点）。
3. 重新发布图文菜单（`rich_menu.publish`），回读每种语言 × 两套都已发布。
4. 用户手机验收：菜单 6 → 三个按钮 → 能播放、封面正确；英文 / 中文手机看到对应语言的菜单。

## 验证状态

- 本地：`check-dms-offline.py` 20 项通过（Python 3.14），`check-architecture.py` 通过。
- 部署、配置、菜单发布、真机验收：未做。
