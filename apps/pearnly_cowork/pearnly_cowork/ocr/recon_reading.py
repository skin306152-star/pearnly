"""对账（recon_center）用的识别读法（纯：不依赖 Frappe、不碰数据库）。服务器本机与 Cloud Run（ADR-015）用同一份，两边读得一样。

- 销项税核查的发票：与发票录入同一个引擎，另加两条（``vat_callbacks``）：抽取结果不明时重发一次、等得更久；
  重发后仍不明的那一页交给引擎的按页兜底，不让整份作废。
- 对账三类文档（银行账单 / 总账 / 销项税报告）：企业档管线（``read_document``）；抽取重发后仍不明的那一页记成读不出
  （``document_page_failure``）。
"""
import dataclasses

from pearnly_core.execution.errors import Uncertain
from pearnly_cowork.ocr.engine.contracts import ModelRequest, ProviderFailure
from pearnly_cowork.ocr.engine.errors import ContentError
from pearnly_cowork.ocr.enterprise import pipeline as enterprise

# 销项税核查：一次运行最多读这么久就让出（续跑时已完成的调用从检查点取回）；抽取每次至少等这么久。
VAT_YIELD_SECONDS = 120
VAT_MODEL_TIMEOUT_SECONDS = 120


def vat_callbacks(client):
    """销项税核查的 (模型调用, 读图调用)，调用方再套自己的守卫（核查已结束 / 让出）。

    重发后仍结果不明的那一页交给引擎的按页兜底，不让整份作废（25 页的扫描件一页不明，25 张发票全丢了，
    2026-09-30 压测第 17 轮 V03）。按引擎每一步接得住的失败给：直读 / 复核给明确失败（引擎转下一步兜底）；
    读原文、带原文再读给「这一页读不出」（有直读结果就沿用并标红，没有就记成这一页没读出）—— 第 21 轮 V04：
    复核之后的一步给了明确失败，引擎没接住，整份当程序错误作废。已发出的那次调用续跑时从检查点认出、同样处理。"""
    last = {}  # 每页最近一次模型调用：(步骤, 是否拿到结果)；多页同时读时按页码分开，互不干扰

    def page_failure(call):
        def invoke(*args, **kwargs):
            request = args[0] if args and isinstance(args[0], ModelRequest) else None
            stage = request.stage if request else kwargs.get("stage", "")
            page = request.page_number if request else kwargs.get("page_number", 0)
            try:
                result = call(*args, **kwargs)
            except Uncertain as error:
                if request:
                    last[page] = (stage, False)
                if stage in ("direct", "recheck"):
                    raise ProviderFailure("ocr.provider_outcome_unknown") from error
                # 读图（Vision）两处用：直读刚成功、中间没复核 → 是「完整税票核对」，给明确失败引擎就跳过核对、保留直读；
                # 其余是读原文兜底，给「这一页读不出」。
                if not request and last.get(page) == ("direct", True):
                    raise ProviderFailure("ocr.provider_outcome_unknown") from error
                raise ContentError("ocr.provider_outcome_unknown", stage=str(stage), page_number=page) from error
            if request:
                last[page] = (stage, True)
            return result
        return invoke

    @page_failure
    def model_call(request):
        # 结果不明（读超时、连接中断）重发一次、等得更久：与账单路径同一口径；发票录入照旧。
        return client.model_call(dataclasses.replace(request, timeout_s=max(request.timeout_s, VAT_MODEL_TIMEOUT_SECONDS)),
                                 retry_unknown=True)

    return model_call, page_failure(client.vision_call)


def document_page_failure(call):
    """一页的抽取重发后仍结果不明（模型读超时、连接中断；续跑时遇到上次没收尾的调用也一样）：换成「这一页读不出」，
    流程只丢这一页、其余页照常（``pipeline.UNREAD_PAGE``；2026-09-30 压测第 23 轮 BAY-CA-03：一页 180 秒读超时，
    12 页整份作废、整套对账失败）。读字（Document AI）结果不明照旧整份作废：没有字就没有页可抽。"""
    def invoke(request, *args, **kwargs):
        try:
            return call(request, *args, **kwargs)
        except Uncertain as error:
            raise ContentError(enterprise.UNREAD_PAGE, stage=str(getattr(request, "stage", "")),
                               page_number=int(getattr(request, "page_number", 0) or 0)) from error
    return invoke


def read_document(source, content, is_pdf, mime, document_type, document_ai_call, model_call):
    """企业档读一份文件 → 旧线上管线结果形状（多页、带 engine）。页面图只在要 3.8 抽取时逐页渲染。"""
    reads = enterprise.pdf_chunks(content, source.page_count) if is_pdf else [(content, mime, 1)]

    def render(number):
        frame = source.frame(number)
        return frame.image_bytes, frame.mime_type

    result = enterprise.run(enterprise.CATEGORIES[document_type], page_count=source.page_count, reads=reads,
        render=render, document_ai_call=document_ai_call, model_call=model_call)
    return enterprise.to_legacy(result)


def image_mime(content):
    import io

    from PIL import Image

    with Image.open(io.BytesIO(content)) as image:
        return Image.MIME[image.format]
