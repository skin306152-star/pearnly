"""文件夹自动录入的运行参数。不是开关：所有 Cowork 租户（含新开户）都有这个功能（用户 2026-10-07：功能不按租户开关）。

- ``ocr_inflight``：同时在实时识别的文件夹单上限；0 = 自动，取商户识别并发的一半，另一半留给网页 / LINE；
- ``push_pending``：每份账套（推送连接 + 账套）同时「录入中」的文件夹票上限，会计手动推的单最多排在这几张后面；
- ``inline_max_mib``：经本站分块上传（不走签名直传时）单个文件的上限。
"""
DEFAULTS = {"ocr_inflight": 0, "push_pending": 5, "inline_max_mib": 15}


def config():
    return dict(DEFAULTS)
