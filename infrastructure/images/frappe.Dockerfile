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
