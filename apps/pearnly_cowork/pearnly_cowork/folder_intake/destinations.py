"""自动录入的推送目标适配：哪种 ERP 能当规则的去向、推送「准备」之后怎么「确认发送」。

全部走现有推送入口（`push.application.service`），这里只登记「确认发送」这一步各家不同的那一小段：
- Express：确认入口要这条连接的小助手凭据（与网页的 `companion.submission.confirm` 同一个判据）；
- 以后要加 MR.ERP 等：在 `ADAPTERS` 登记一个新类，`supported()` 就自动放行，规则、受理、表格都不用改。
  （MR.ERP 的确认在 `application.submit` 里已经顺带排上服务端执行，适配类的 `confirm` 什么都不用做。）

注意：读文件夹的永远是这条连接的 Windows 小助手（配对码身份），所以目前只登记 Express。
"""
from pearnly_cowork.companion import credentials
from pearnly_cowork.push.application import service as application


class ExpressTarget:
    adapter = "express"
    intake_target = "express"

    @staticmethod
    def confirm(connection, prepared, request_key):
        return application.confirm(prepared["name"], prepared["revision"], request_key,
                                   credential=credentials.for_confirmation(connection))


ADAPTERS = {ExpressTarget.adapter: ExpressTarget}


def supported(adapter):
    return adapter in ADAPTERS


def of(adapter):
    return ADAPTERS[adapter]
