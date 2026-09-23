# Cowork 原件与原生 File 守卫

日期：2026-09-21。所属 App / 域：`pearnly_cowork.files`。
本记录对应独立工作树 `cowork-backend-migration` 的本地实现；不代表部署、历史文件迁入或用户验收。

## 责任与入口

- `files/access.py`：原件整份/物理页授权、原生 File 身份及别名识别、权限和列表 hooks。
- `files/guard.py`：Frappe `extend_doctype_class` 的 File mixin、可信进程内读写 capability、原件精确字节写入。
- Source DocType、`files/service.py` / `files/api.py`、历史整批绑定、hooks 和 Site 测试由本工作包主任务维护；此子任务仅修改上面两个模块及本记录。
- 依赖方向：files → access.scope / ocr.inputs；后者仅复用上传大小常量。普通 File 仍走锁定 Frappe 的实现。没有网络读取、云操作或外部业务写入。

## 授权约定

`Cowork Source File` 保存上传者、初始公司/客户、实际页数、精确字节 SHA-256、不可变 File ID / storage_url 和原生 `native_content_hash`。原生 hash 是 MD5 去重键，只用于识别跨 File 副作用，不能替代 SHA-256 完整性检查。

历史记录的 `source_file` / 物理页映射与完整绑定清单由可信整批入口一次性写入。只有 `binding_complete=1` 才允许页预览；同一物理页涉及的每条历史都必须有权限，不能因为本票可读就暴露同页另一账套的票据。非上传者整份下载还要求覆盖全部物理页，并有初始范围及全部关联记录范围权限。上传者已有原始字节，可以在绑定前读回；每次仍重查初始范围和全部已知关联范围。

File owner、DocShare、管理员身份或 JSON flags 不是业务文件读取能力。原件的原生私有 URL、通用 File metadata 读取和普通 `get_content` 均拒绝；只有真正的 `READ_CAPABILITY` / `WRITE_CAPABILITY` 对象可读取 bytes。业务端点独立验证权限并设置 `no-store` / `nosniff`。能力对象不保存到数据库，不接受客户端构造。

## 对 Frappe 16.33.0 的适配

审核使用停止的专有 lab 镜像中复制出的实际源码，未启动或修改容器。参考副本位于 `/tmp/pearnly-cowork-migration-20260920/frappe-file-review`。

| 原生行为 | 自有守卫 |
| --- | --- |
| `utils/response.py:301` → `file/utils.py:452` → `File.is_downloadable`；owner / share 先于 attached_to 授权 | 原件直接下载一律拒绝；`get_content` 同时守住 ZIP / handler 路径 |
| `file.py:732` 将站点绝对 URL 转为本地路径，并允许站点内不同路径表达 | 统一解析编码、绝对 URL、点路径及符号链接；同时检查数据库旧值和请求新值 |
| `file.py:889-890` 分别删除 file_url 和 thumbnail_url | 缩略图字段也纳入原件物理路径识别，禁止借普通附件的缩略图引用删原件 |
| `file/utils.py:300` 按 content_hash 批量修改其他 File 的 URL / 隐私属性 | 原件同 hash 的修改拒绝；查询用 current read，避免 RR 快照漏掉新建 Source |
| `file.py:949` optimize 写文件后才 save，`:353` validate 内移动文件，`:560` thumbnail 直接写文件，`:636` unzip 直接读路径 | 在对应方法和底层写入/删除方法执行前拦截；不只依赖 validate |
| `model/document.py:1403-1407` 在 ignore_validate 判断前调用 before_validate | 同时使用 before_validate，JSON `ignore_validate` 不能关闭守卫 |
| `permissions.py:481-498` 将多个 has_permission hook 按 AND 检查，None 同样拒绝 | 普通 File 返回 True 继续原生权限判断；受保护 File 抛 PermissionError 阻断 share fallback |
| 原生列表权限及 DocShare OR 回退会扩大结果 | 原件从通用列表排除；发现能绕过列表过滤的残留原件分享时拒绝列表，新分享也拒绝 |

独立物理路径但与原件内容 hash 相同的普通附件可以按原生权限读取；会被禁止执行可能连带影响原件的修改。无关普通 File 的下载、大小限制、保存、优化及删除继续调用 `super`。

## 原件写入及回滚

只有 WRITE capability、Source attachment 和新建 File 同时成立，才使用原件写入分支。它复用原生文件名防碰撞、内容安全检查、存储 hooks、文件系统写入和回滚机制，省去 JPEG EXIF 转换与跨 File 去重，不修改任何全站设置。已落库原件不得经此分支覆盖。

大小上限复用 `ocr.inputs.MAX_FILE_BYTES`，目前为 100 MiB；普通 File 的站点上限不变。`get_content(encodings=[])` 保留 bytes，业务服务落库后回读核对 SHA-256。独立原件回滚按实际 URL 引用判断清理，不沿用“相同 MD5 即共享物理文件”的假设，避免留下孤儿原件；普通失败插入若指向受保护别名，也不能清理他人的原件。

此实现使用原生私有文件。旧源码 `services/ocr/pdf_backfill.py:35-45` 可能先生成可搜索 PDF，`pdf_storage` 还支持可选落盘加密；它们与精确上传 bytes / 原生私有文件不同。此批没有迁入真实旧文件，不能据此声称旧文件加密和解密迁移已经验收。

## 验证与限制

本子任务执行：两个模块 `py_compile` 通过；`python3 scripts/check-architecture.py` 通过（0 violations）。临时离线测试 `/tmp/pearnly-cowork-migration-20260920/test_file_guards_offline.py` 的 16 项通过，覆盖路径/缩略图旧值、封存前权限、共享物理页、假 capability 拒绝、原件各修改入口、普通附件转交原生、100 MiB 边界和回滚分支。

离线测试通过 AST 加载实际生产函数，明确替换 Frappe/数据库和原生 File 外部边界；它验证本模块判定与调用，不代表真实 Site、SQL 并发或 HTTP 验收。统一真实 Site / HTTP 检查入口为 `scripts/cowork-files-lab-verify.py`，运行结果及镜像、Site、源码记录由主任务登记。子任务没有执行 migrate、Site 写入或容器启停。

必须保留的真实场景包括：跨账套共享 PDF、未完成绑定不能预览、同页双票、owner/share 拒绝、绝对/编码/点路径和缩略图别名、同 hash 不同 URL 的隐私切换、普通 File 原生 metadata 权限、精确 JPEG bytes、超过原生默认大小但未超过 100 MiB 的原件、越界拒绝、事务失败后的文件清理、业务下载响应头和并发幂等。
