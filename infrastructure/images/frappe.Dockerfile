# pearnly 样板基础镜像
#
# 来源是官方 frappe/erpnext，**按 digest 锁定**，不用浮动 tag。
# 实测该镜像内含 Frappe 16.33.0 + ERPNext 16.34.1 + Python 3.14.7，
# 与本项目锁定的版本矩阵一致（见 docs/p1/01-version-matrix.md）。
#
# 本文件当前只固定来源与署名，不添加业务代码。P1/P3 会把 pearnly_core 等
# 自有 App 通过 COPY 加进来，届时本文件就是这些 App 的唯一构建入口——
# 这是 ADR-003「本仓是唯一权威位置」在镜像层面的落地。

ARG FRAPPE_BASE=frappe/erpnext:v16.34.1@sha256:eee240a179ff494cb82ad067d853c8db3f87171d86324f4b326892654e342144

FROM ${FRAPPE_BASE}

LABEL org.pearnly.image.role="frappe-sample-base" \
      org.pearnly.image.frappe.version="16.33.0" \
      org.pearnly.image.erpnext.version="16.34.1" \
      org.pearnly.image.python.version="3.14.7" \
      org.pearnly.image.upstream.digest="sha256:eee240a179ff494cb82ad067d853c8db3f87171d86324f4b326892654e342144" \
      org.pearnly.image.note="arm64;目标机是 x86_64，构建目标须显式指定平台"

# 自有 App。基础镜像里的 frappe/erpnext 都是 editable 安装（指向 apps/<name>），
# 这里保持一致：拷进 apps/ 并做 editable 安装，否则模块不可导入。
# 构建上下文必须是仓库根目录，例如：
#   docker build -f infrastructure/images/frappe.Dockerfile -t pearnly/frappe-sample:16.34.1 .
COPY --chown=frappe:frappe apps/pearnly_core /home/frappe/frappe-bench/apps/pearnly_core
RUN cd /home/frappe/frappe-bench && env/bin/pip install --no-deps -e apps/pearnly_core

# /assets/<app>/... 由 assets 目录里的软链提供。上游镜像构建时只给 frappe 和
# erpnext 建了软链，自有 App 要自己补，否则前端资源 404（踩过）。
RUN ln -s /home/frappe/frappe-bench/apps/pearnly_core/pearnly_core/public \
          /home/frappe/frappe-bench/assets/pearnly_core
