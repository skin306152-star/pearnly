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
    def ready(prepared):
        """准备时小助手报上了这份账套才能确认；没报上（小助手刚好重启、断线）确认必然失败，
        自动录入撤掉这次准备、过一会儿重来，而不是记成失败（网页「提交」仍照原样提示）。"""
        return bool(prepared.get("has_reported_credential"))

    @staticmethod
    def confirm(connection, prepared, request_key):
        return application.confirm(prepared["name"], prepared["revision"], request_key,
                                   credential=credentials.for_confirmation(connection))


ADAPTERS = {ExpressTarget.adapter: ExpressTarget}


def ready(adapter, prepared):
    target = ADAPTERS[adapter]
    return target.ready(prepared) if hasattr(target, "ready") else True


def supported(adapter):
    return adapter in ADAPTERS


def of(adapter):
    return ADAPTERS[adapter]
