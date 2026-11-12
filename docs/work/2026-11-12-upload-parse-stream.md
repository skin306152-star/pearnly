# 上传边收边写盘 · 第 1 步：网页入口经 `pearnly_core.web.wsgi`（ADR-028）

分支 `pearnly/upload-parse-stream`，基线 origin/main 6275e4999。主会话 11-12 审定评估后批准（共享笔记「C线-上传边收边写盘-评估-待审-11-12」，
含线上 11-12 内存回读的解释与只读命令）。第 2 步（各上传接口不再把文件读成 bytes）另一支。

## 改了什么

- 新增 `apps/pearnly_core/pearnly_core/web/`：`multipart.py`（只换 multipart 一路的 `make_form_dict`，钉 Frappe 16.33.0 源码指纹）、
  `wsgi.py`（进程入口：装好替换再导出 Frappe 的 `application`；`python -m pearnly_core.web.wsgi <端口>` 同 `bench serve --noreload`）。
- 运行定义：生产 compose 五处 gunicorn 应用改为 `${PEARNLY_WSGI_APP:-pearnly_core.web.wsgi:application}`；回归实验室、场景预检、
  本机开发（01-bench）与三份预览编排同用新入口。
- 测试：`tests/test_web_multipart.py`（候选离线车道，生产镜像里跑：逐项与 Frappe 原实现比对、非 multipart 原样交回、40MB 不进内存、指纹）；
  `tests/test_web_entry.py`（Development checks：每处运行定义都经新入口、回退一行、候选车道已登记）。
- ADR-028：为什么不能用钩子、改了哪一段、Frappe 升级怎么办、回退。

## 回退（同 ADR-028）

生产 `/opt/pearnly/secrets/runtime.env` 加 `PEARNLY_WSGI_APP=frappe.app:application`，按发布流程重新激活四个产品；
读回 `docker inspect -f '{{json .Config.Cmd}}' <网页容器>` 末尾是 `frappe.app:application`。部署记录里照抄这一段。

## 验证

（见下方各节，按做完的顺序补）
