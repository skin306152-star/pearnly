# 老产品的连接凭据已找到并接入（不用再问用户要）

日期：2026-09-18。承接 [15-ocr-ported.md](15-ocr-ported.md) 结尾那句"这四件的共同前置是 DMS 的连接凭据"。

用户当时的话是：**"老项目都有呀，没有你就自己打开我本机浏览器去找，啥都有。"**
这次确实找到了 —— 记下来，省得下回又去问。

## 一、找到什么、在哪

| 东西 | 位置 | 形态 |
|---|---|---|
| MR.ERP / DMS 连接凭据 | `~/.config/pearnly-migration/20260905/supabase-before.dump` 的 `erp_endpoints` 表 | **Fernet 加密**（`config.username_enc` / `password_enc` / `admin_*_enc`） |
| 解密密钥 | `~/.config/pearnly-migration/20260905/{web,worker,legacy}.env` 的 `PEARNLY_KMS_KEY` | 明文（46 字符 = Fernet key 长度） |
| LINE 全套凭据 | 同上 env 文件的 `LINE_*` | 明文；已存成 `~/.config/pearnly/credentials/legacy-line.env`（0600） |
| 端点表结构 | 老产品 `erp_endpoints` DDL | 和我们的 `Pearnly ERP Endpoint` 字段一一对上 |

老产品把凭据加密存库（`kms_helper.encrypt_str`，见 `docs/integrations/mrerp-known-facts.md` §10
"凭据存储铁律"），所以 dump 里看到的是密文 —— **密钥也在本机**，能解。

## 二、读 dump 踩的坑：`pg_restore` 版本

`supabase-before.dump` 是 **PG17 的自定义格式（archive version 1.16）**，
容器里的 `pg_restore 15` 直接拒读：`unsupported version (1.16) in file header`。
本机没装 PG17 客户端，解决方式是借一个一次性容器：

```bash
docker --context colima-pearnly run --rm \
  -v "$HOME/.config/pearnly-migration/20260905:/d:ro" \
  -v "$HOME/.config/pearnly-migration/20260918:/out" postgres:17-alpine \
  sh -c 'pg_restore -a -t erp_endpoints -f - /d/supabase-before.dump > /out/erp_endpoints.sql'
```

两个小坑：`pg_restore` 没有 `--inserts/--column-inserts`（那是 `pg_dump` 的选项），
且必须给 `-f -` 才会写标准输出；`docker cp` 进容器的文件是 root 0600，
容器里以 `frappe` 身份跑会 `Permission denied`。

## 三、用哪个账号：`dmstest`，不碰客户账号

dump 里 22 条 DMS 连接分属不同账号。**只能拿测试账号做实验**，所以脚本
`scripts/legacy-endpoint-to-json.py` 强制 `--require-username dmstest`，
挑不到就报错退出，绝不放任"随便挑第一条"。

账号身份是从老产品自己的文档确认的，不是猜的：

- `docs/integrations/mrerp-dms-ui-test-plan.md:35` → **DMS 测试账号 `dmstest`**
- 解出来 13 条 `dmstest` 连接，密码与账号同名（长度 7），`system_url` 全部一致

⚠️ 同一份文档还有一条运维约束要记住：**`dmstest` 只允许一个活跃登录**
（`mrerp-dms-recovery.md:5`）。跑 DMS 链路前先确认没有别的会话占着，
否则两边会互相踢下线。

## 四、已接入我们的站点

`scripts/sample-seed-dms-endpoint.sh dms qd65956.localhost` → **`EP-00029`**（15 项全过）：

| 项 | 值 |
|---|---|
| 适配器 | `mrerp_dms` |
| 系统地址 | `https://www.mrerp4sme.com/dms/index.php` |
| 账号 | `dmstest`（另带一套同名的"有修改权限"账号） |
| `auto_push` | **0** —— DMS 连接绝不能参与发票自动推送（老产品踩过，见 `mrerp-dms-integration-handoff.md:72`） |
| 密码存放 | Frappe `Password` 字段 → `__Auth`，**主表列里是掩码**（脚本里有一条断言专门钉这个） |

明文一律只落 `~/.config/pearnly/credentials/`（0600），**不入版本库、不进对话**；
终端只打印脱敏结果。

## 五、可复用的脚本

| 脚本 | 干什么 |
|---|---|
| `scripts/import-legacy-endpoints.py` | 读 COPY 导出 → Fernet 解密 → 写 `~/.config/pearnly/credentials/legacy-endpoints.json` |
| `scripts/legacy-endpoint-to-json.py` | 从上面那份里**按账号名精确**挑一条 |
| `scripts/sample-seed-dms-endpoint.sh` | 挑出 `dmstest` → 写进站点 → 回读校验 15 项 |

换机器（或 dump 更新了）重跑这三条即可，不用重新去库里挖。

## 六、还没做的

- **真连一次 DMS**：登录是 Playwright 真浏览器（老产品 `mrerp_dms_client.py`
  注释原文："the DMS session is owned by a real Playwright browser login"），
  客户端还没搬过来 —— 这是清单上的下一件。
- **LINE 真回调**：凭据齐了，但 `*.localhost` 收不到 LINE 的公网回调，
  需要公网 HTTPS 地址才能真正验证（接口按租户隔离的部分照计划做）。
