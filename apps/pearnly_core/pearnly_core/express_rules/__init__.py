"""Express（泰国会计软件）写账规则：多产品共用（Cowork 推送、ERP 直连 CC，ADR-025）。

纯函数，不连库、不依赖 Frappe、不做 IO。由 `pearnly_cowork.push.express` / `contracts` / `targets` / `push.receipts`
原样搬来（2026-10-23），逻辑一字未改；产品各自的流程（账本、权限、要人选的记忆）留在产品里。
"""
