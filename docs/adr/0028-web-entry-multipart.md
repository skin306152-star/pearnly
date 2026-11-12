# ADR-028：网页进程入口经 `pearnly_core.web.wsgi`，multipart 请求不再整份读进内存

- 状态：**已采纳**（2026-11-12，主会话审定评估后批准施工；C 上传线施工）。仓库第一处替换 Frappe 框架入口里的一段逻辑。
- 关联：评估（共享笔记「C线-上传边收边写盘-评估-待审-11-12」）、[压测第二轮](../work/2026-11-11-prod-stress-round2.md)（40MB 并发，问题 R2-4）、
  [上传上限 40MB](../work/2026-11-10-upload-size-limits.md)、[网页容器内存](../work/2026-11-11-web-container-memory.md)、任务记录 [2026-11-12-upload-parse-stream](../work/2026-11-12-upload-parse-stream.md)。

## 给用户看的一段话

传大文件时，服务器原来要先把整份文件读进内存、再复制一份文本，40MB 的文件一下子多占约 150MB；十几个人同时传就把网页容器挤满、变慢。
现在文件一边收一边写到磁盘的临时文件里，内存几乎不涨；收到的内容、页面看到的结果和原来完全一样。

## 问题

Frappe 16.33.0 的 `frappe.app.make_form_dict` 在 `init_request` 里、任何钩子（`before_request`）之前执行，第一行
`request.get_data(as_text=True)` 把整个请求体读进内存、缓存到请求结束（werkzeug `_cached_data`），并转成一份文本。
这份文本只有 JSON 请求才用；multipart（文件上传）请求走 else 那一支、只用 `request.args` 与 `request.form`，读进来的这份只是顺带缓存住。
生产镜像实测：一份 40MB 上传峰值 +152MB、常驻 40MB；压测第二轮 15 份同时传，Cowork / ERP 网页容器到上限的 94% / 97%、进 swap。
不调这一行时，werkzeug 自己就把 multipart 里的文件按块落到临时文件（实测 +0MB）。

## 为什么不能用钩子

- Frappe 的钩子都在 `make_form_dict` 之后：`before_request` 跑的时候请求体已经读完缓存了；没有「请求解析前」的钩子。
- 应用模块是第一次请求时才按 hooks 导入的，在应用里改模块属性，每个进程的第一个请求仍按原样处理，而且什么时候生效说不清——不可靠，也不可验。
- 在 nginx / 前置服务上改不了：读请求体的是 Frappe 进程本身。
所以在进程入口（gunicorn 的 WSGI 应用、本机开发服务器）就先装好替换，再交出 Frappe 自己的 `application`。

## 决定

1. **新入口** `pearnly_core.web.wsgi`：导入时调用 `pearnly_core.web.multipart.install()` 替换 `frappe.app.make_form_dict`，导出 `frappe.app.application`
   （本机开发用 `application_with_statics()`、`python -m pearnly_core.web.wsgi <端口>` 同 `bench serve --noreload`）。
   - 生产：compose 五处 gunicorn（x-web 与 billing / dms / erp / cowork 网页）的应用都是 `${PEARNLY_WSGI_APP:-pearnly_core.web.wsgi:application}`；
   - 回归实验室、场景预检、本机开发与预览编排同用这个入口。
2. **只换 multipart 那一路，结果与原实现逐项相同**（`pearnly_core/web/multipart.py`）：
   - 不是 `multipart/form-data`，或不带 Content-Length（流式上传，浏览器不会这样传）→ 整个请求原样交给原函数，一字不改；
   - multipart：解析表单时经一个只读包装把读到的原始字节写进临时文件（1MB 以内在内存），解析完把解析器没读到的尾巴也读进去，
     `request.stream` 留一份同样的剩余；这个请求的 `get_data` 换成按需从临时文件读，`request.data`、Frappe 的 OAuth 授权、REST v1、
     本仓机器通道（`cc_desktop` / Cowork companion 的「请求体不是 JSON 就拒」）读到的原始字节与原来一字不差；
   - `frappe.form_dict` = 查询串 + 表单、去掉 `_`（原实现 else 那一支）；超限一样在读流时 413；临时文件在响应发出后关掉。
   - 对比测试 `tests/test_web_multipart.py`（候选离线车道，生产镜像里跑）逐项比 `form_dict`、`files`、`form`、`args`、`get_data` 三种调法、`data`、剩余流：
     Desk 原生 `upload_file`、带普通字段、字段值像 JSON、非 UTF-8 / 泰文文件名、空文件、多文件、只有字段、没有部分、结尾附言、坏的 multipart、
     超过 werkzeug 内存阈值的文件、Content-Length 超限、流式超限；另核非 multipart（JSON、urlencoded、GET、纯文本、`+json`）原样交给原函数。
     同一套样例里，werkzeug 原本就会整份丢弃非 UTF-8 文件名那一部分，两边一样。
3. **钉住 Frappe 源码**：`MAKE_FORM_DICT_SHA256` 是 Frappe 16.33.0 `make_form_dict` 源码（`inspect.getsource`）的 sha256。
   - 测试：候选离线车道比对，底座一变就红（构建与部署都过不去），逼着人重新核对；
   - 运行时：指纹对不上就不替换、照原样收，并在 stderr 写一行（`ADR-028`）。宁可回到原来多占内存，也不把没审过的 Frappe 逻辑换掉。
4. **不做租户开关**；它不改任何业务结果，按进程生效。

## Frappe 升级时怎么办

1. 候选离线车道 `test_frappe_make_form_dict_is_the_reviewed_one` 变红。
2. 读新版 `frappe/app.py` 的 `make_form_dict` 与 werkzeug `Request.get_data` / `_load_form_data`：multipart 是否仍只用 `args` + `form`、是否还有别的副作用。
3. 照新版调整 `web/multipart.py` 的 multipart 那一支，`tests/test_web_multipart.py` 全过（尤其逐项比对与 413），再改 `MAKE_FORM_DICT_SHA256`。
4. 如果新版 Frappe 已经不在 multipart 请求上整份读请求体，就删掉替换，入口只留导出（或改回 `frappe.app:application`），删本 ADR 的第 2、3 条。

## 回退

运行定义里把入口换回 Frappe 原生的一行：生产 `/opt/pearnly/secrets/runtime.env` 加
`PEARNLY_WSGI_APP=frappe.app:application`，按发布流程重新激活四个产品（网页容器按新命令重建）。读回：
`docker inspect -f '{{json .Config.Cmd}}' <网页容器>` 末尾是 `frappe.app:application`。本机实验栈回退：`regression-lab.yml` 那一行换回 `bench serve --port $$p --noreload`。
回退只让上传重新多占内存，不影响业务数据。

## 后果

- 每份 40MB 上传在网页进程里少约 150MB 峰值、40MB 常驻；第 2 步（各上传接口不再把文件读成 bytes）另行交付。
- 磁盘：multipart 原始字节多写一份临时文件（请求结束删除）；页面缓存可回收，不算匿名内存。
- 维护：每次 Frappe 升级多一项人工核对（第 3 条），由测试强制。
