"""代录 Express 的服务端：多产品共用（Cowork 与 ERP 同一套，ADR-009 / ADR-025；评估 docs/work/2026-10-26-express-operator-product-评估.md）。

CC 桌面版在办公室电脑上真实打开 Express 一张张录单（执行在 ``companion/src/companion/express_operator``），与直写是两种执行方式、
入口分开。服务端这一侧只管：代录批次与每张单的结果、发起权限、对比报告。共用层不认识产品的表：

- 产品在自己的 ``hooks.py`` 声明 ``pearnly_express_operator``（谁能发起、单据从哪来、结果交回给谁，见 ``products``）；
- 单据来源各接各的（Cowork 的识别草稿、ERP 的采购 / 销售单据），都只出直写同一份载荷；
- 纯规则在 ``contracts``（状态、原因代码与三级、整批停的原因，与 CC 端 ``contract`` 一字不差，``tests/test_express_operator_shared.py`` 守住），
  对比报告在 ``report``（同一批单据：代录 / 现有直写 / 会计手录）。

批次表、接口与派单随共用件那一步接（评估 §7），这里先放与框架无关的部分。
"""
