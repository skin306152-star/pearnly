"""企业档（Enterprise）财务文档识别：Document AI 读字 + 本地按版面拼行，算不平才让 3.8 LOW 看图抽取。

来源：旧线上 ec2e5c957e6b（2026-09-05 22:19 起银行 / 总账 / 税报的扫描件与照片都走它，见旧仓
``docs/deployment/MIGRATION_STATUS.md``）。``contract`` 与 ``local/*`` 逐字照搬；``pages`` / ``request`` / ``adapter``
只改导入与错误类型；``pipeline`` 把服务商调用换成注入的 Document AI 与模型调用（在持久识别任务里跑，检查点防重复付费）。
纯计算与编排，不连库、不读文件；服务商调用由调用方注入。
"""
