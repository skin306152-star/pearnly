# pearnly 样板基础镜像
#
# 来源是官方 frappe/erpnext，**按 digest 锁定**，不用浮动 tag。
# 实测该镜像内含 Frappe 16.33.0 + ERPNext 16.34.1 + Python 3.14.7，
# 与本项目锁定的版本矩阵一致（见 docs/p1/01-version-matrix.md）。
#
# CI 构建复用上一次**验证通过**镜像的层作为缓存（`build-production.yml`），所以这里每一步都必须
# 是确定的：依赖钉死版本 / digest，不写"装最新"这类每次结果可能不同的步骤，否则会沿用旧层。
#
# 自有 App 与部署工具一起 COPY 进不可变镜像。本机验收与正式发布使用同一入口，
# 显式构建 linux/amd64，与目标机一致；开发源码挂载不作为发布验收证据。

ARG FRAPPE_BASE=frappe/erpnext:v16.34.1@sha256:eee240a179ff494cb82ad067d853c8db3f87171d86324f4b326892654e342144

FROM ${FRAPPE_BASE}

LABEL org.pearnly.image.role="frappe-sample-base" \
      org.pearnly.image.frappe.version="16.33.0" \
      org.pearnly.image.erpnext.version="16.34.1" \
      org.pearnly.image.python.version="3.14.7" \
      org.pearnly.image.upstream.digest="sha256:eee240a179ff494cb82ad067d853c8db3f87171d86324f4b326892654e342144" \
      org.pearnly.image.note="构建目标须显式指定 linux/amd64，与生产目标机一致"

# OCR 走 Vertex 时要的认证库（服务账号 JSON → access token）。
#
# 为什么不自己签 JWT：那要再拖一个 `cryptography`（编译依赖、镜像更大），
# 而 `google-auth` 是官方库、老产品生产也在用同一套认证语义。
#
# ⚠️ **凭据本身不进镜像**：这里只装库，服务账号 JSON 由运行时挂载进来
# （本机开发见 `infrastructure/compose/04-dev-mounts.yml` 的 credentials 挂载；
# 生产走 Secret/环境变量，与老产品 `GOOGLE_APPLICATION_CREDENTIALS` 一致）。
RUN /home/frappe/frappe-bench/env/bin/pip install --no-cache-dir "google-auth==2.36.0"

# PDF 首页栅格化（身份证/发票走共享上传流水线时会被包成单页 PDF）。
#
# 为什么必须装：老产品的注释写得很清楚 —— 相册/相机图片在到达接口前会经
# `imagesToPdf` 变成 `photo_*.pdf`，而识别器只吃图片，不解的话直接报"读不出"。
#
# ⚠️ **版本与老产品不同，原因是我们 Python 更新**：老产品锁 `pymupdf==1.24.10`
#    跑在 Python 3.11 上；本镜像基础层是 **Python 3.14**，1.24.10 没有 cp314 轮子，
#    pip 会退回源码编译并失败（实测）。
#    1.28.2 提供 `cp310-abi3` 轮子（abi3 = 跨 3.10+ 通用），3.14 直接可用。
#    这是版本跟随，不是换库：调用方式与渲染语义都没变。
RUN /home/frappe/frappe-bench/env/bin/pip install --no-cache-dir "pymupdf==1.28.2"

# Cowork electronic-PDF text path: freeze the source lock and current base
# runtime version. Text extraction must not silently fall back to paid Vision
# merely because a future base image changes its transitive dependencies.
RUN /home/frappe/frappe-bench/env/bin/pip install --no-cache-dir "pypdf==6.15.0"

# Cowork structured originals. Keep the native base's lxml patch version;
# table/Word parity is checked against the source lock separately.
RUN /home/frappe/frappe-bench/env/bin/pip install --no-cache-dir \
    "openpyxl==3.1.5" "et-xmlfile==2.0.0" "python-docx==1.2.0" "lxml==6.1.2"

# 识别前的本地转正（ADR-013，`pearnly_core.ocr.orient`）：PaddleOCR 方向分类模型（`ocr_models/doc_ori.onnx`，
# Apache-2.0）用 onnxruntime 在 CPU 上跑，约 15 毫秒一张、不调任何 API。版本钉死；三个包都有 cp314 manylinux 轮子，
# 实测连同依赖使环境增大约 136 MB，`pip check` 无新增冲突（2026-09-27，用户同意加这项底座改动）。
RUN /home/frappe/frappe-bench/env/bin/pip install --no-cache-dir \
    "onnxruntime==1.30.0" "numpy==2.5.3" "flatbuffers==25.12.19"

# ── 浏览器：DMS 是"真人在网页上操作"的系统，没有开放 API ─────────────────────
#
# 这不是测试工具，是**运行时依赖**：老产品的铁律是"无开放 API 的 ERP 一律走
# 服务端 Playwright，不做抓包重放"（`AGENTS.md` §7）。DMS 会话由真实浏览器登录
# 持有，业务客户端只拿它当 transport。
#
# 三个要点，缺一个都会在运行时才炸：
#
# 1. `--with-deps` 装的是系统库（libnss3 / libatk 等），**必须 root 跑**；
# 2. 浏览器**不能**落在 `/root/.cache` —— 容器以 `frappe`(1000) 身份跑，
#    读不到 root 的家目录。所以显式钉 `PLAYWRIGHT_BROWSERS_PATH`，
#    让"安装"和"运行时"看到同一个路径（老产品是 root 跑，用的是默认路径，
#    这里身份不同，不能照抄那一段）；
# 3. 版本对齐老产品 `requirements.lock.txt`：`playwright==1.60.0`。
#
# 本机容器 `/dev/shm` 只有 64M（实测），所以 `_browser.py` 里那句
# `--disable-dev-shm-usage` 是必需的，不是可有可无的优化。
ARG PLAYWRIGHT_VERSION=1.60.0
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright
USER root
RUN /home/frappe/frappe-bench/env/bin/pip install --no-cache-dir "playwright==${PLAYWRIGHT_VERSION}" \
 && /home/frappe/frappe-bench/env/bin/playwright install --with-deps chromium \
 && chmod -R a+rX /opt/ms-playwright
USER frappe

# Stable runtime dependencies precede source COPY so UI changes reuse these layers.
# 自有 App。基础镜像里的 frappe/erpnext 都是 editable 安装（指向 apps/<name>），
# 这里保持一致：拷进 apps/ 并做 editable 安装，否则模块不可导入。
# 构建上下文必须是仓库根目录，例如：
#   docker build -f infrastructure/images/frappe.Dockerfile -t pearnly/frappe-sample:16.34.1 .
# /assets/<app>/... 由 assets 目录里的软链提供；上游只为 frappe 和 erpnext 建了
# 软链，自有 App 必须自己补，否则前端资源 404（踩过）。
COPY --chown=frappe:frappe apps/pearnly_core       /home/frappe/frappe-bench/apps/pearnly_core
COPY --chown=frappe:frappe apps/pearnly_cowork     /home/frappe/frappe-bench/apps/pearnly_cowork
COPY --chown=frappe:frappe apps/pearnly_erp_portal /home/frappe/frappe-bench/apps/pearnly_erp_portal
COPY --chown=frappe:frappe apps/pearnly_dms        /home/frappe/frappe-bench/apps/pearnly_dms
COPY --chown=frappe:frappe apps/pearnly_control    /home/frappe/frappe-bench/apps/pearnly_control

RUN cd /home/frappe/frappe-bench && \
    for app in pearnly_core pearnly_cowork pearnly_erp_portal pearnly_dms pearnly_control; do \
      env/bin/pip install --no-deps -e "apps/$app" && \
      ln -s "/home/frappe/frappe-bench/apps/$app/$app/public" "/home/frappe/frappe-bench/assets/$app"; \
    done

# 产品 Desk 脚本 / 样式按 Frappe 官方做法打包（2026-10-01：ERP 首次打开原要拉 68 个零散文件）：先按各 App hooks 里的
# DESK_JS / DESK_CSS 清单生成官方入口（*.bundle.js / *.bundle.scss），再 `bench build --production` 用 esbuild 打成
# 带内容哈希的压缩文件、登记进 assets.json。`bench build` 只认 sites/apps.txt 里的 App：构建时临时写上，打完恢复原样
# （运行时 sites 是数据卷，里面的 apps.txt 不受影响）；assets.json 要写进镜像里那份（/home/frappe/frappe-bench/assets，
# 运行时入口把 sites/assets 链到它），所以构建时也先这样链上、打完拿掉。不联网（esbuild 在 frappe 的 node_modules 里）。
# 见 pearnly_core.desk.assets。
COPY --chown=frappe:frappe scripts/build-desk-bundles.py /tmp/build-desk-bundles.py
RUN cd /home/frappe/frappe-bench && cp sites/apps.txt /tmp/apps.txt && \
    printf 'frappe\nerpnext\npearnly_core\npearnly_cowork\npearnly_erp_portal\npearnly_dms\npearnly_control\n' > sites/apps.txt && \
    rm -rf sites/assets && ln -s /home/frappe/frappe-bench/assets sites/assets && \
    for app in $(env/bin/python /tmp/build-desk-bundles.py apps); do \
      bench build --app "$app" --production || exit 1; \
    done && \
    rm -f sites/assets && cp /tmp/apps.txt sites/apps.txt && rm -f /tmp/apps.txt /tmp/build-desk-bundles.py

# ── 宿主侧部署工具的副本（激活时用来核对服务器上那份是不是同一版） ─────────────
#
# 为什么不省这一步：镜像和 `/opt/pearnly/deploy` 是**两份拷贝**，发布时两半都要换。
# 只换一半不会立刻报错（站点跑新代码、开户还用旧脚本），所以激活脚本会把这里的内容
# 和服务器上那份逐文件比对（scripts/production/check-deploy-image.py），不同源就拒绝激活。
# 这里只放"部署拷贝真正包含"的两个目录，其他（compose/、docs/、示例脚本）不进镜像。
COPY --chown=frappe:frappe infrastructure/production /opt/pearnly/deploy-src/infrastructure/production
COPY --chown=frappe:frappe scripts/production          /opt/pearnly/deploy-src/scripts/production
