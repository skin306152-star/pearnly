"""按模型把模型调用交给对应的供应商适配层（ADR-017）。

引擎只认一个 ``model_call`` / ``vision_call``：请求里的模型是 Claude 的交给 ``AnthropicClient``，其余照旧交给谷歌的
``ProviderClient``。读字（Vision）、Document AI 与其余属性都是谷歌那一个的，原样转过去。
"""
from pearnly_cowork.ocr.providers.anthropic import is_claude


class ModelRouter:
    def __init__(self, base, claude=None):
        self._base, self._claude = base, claude

    def model_call(self, request, **kwargs):
        if is_claude(getattr(request, "model", None)):
            if self._claude is None:
                from pearnly_cowork.ocr.providers.client import ProviderConfigurationError

                raise ProviderConfigurationError("ocr.anthropic_not_configured")
            return self._claude.model_call(request, **kwargs)
        return self._base.model_call(request, **kwargs)

    @property
    def claude(self):
        """Claude 的客户端（没配为 ``None``）：批处理（ADR-018）组请求、取请求头用。"""
        return self._claude

    def __getattr__(self, name):
        return getattr(self._base, name)
