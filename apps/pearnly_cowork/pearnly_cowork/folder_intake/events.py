"""识别一结束就接着推进文件夹录入（`cowork_ocr_recognized` 钩子，排在 LINE 之后）。

只对文件夹交来的识别单（请求键 `folder:` 开头）排一个推进任务，不查库、不写库；推进本身照旧读本功能自己的表，
所以别处的请求键碰巧也这样开头，最多多跑一次推进，不会改动任何东西。定时器每分钟的推进照旧兜底。
"""


def recognized(batch):
    if str(batch.get("request_key") or "").startswith("folder:"):
        from pearnly_cowork.folder_intake import pump

        pump.kick()
