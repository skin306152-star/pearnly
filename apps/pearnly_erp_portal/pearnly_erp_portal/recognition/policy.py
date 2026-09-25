"""ERP 采购 / 销售识别的调用策略：照旧线上发票识别的实际运行配置。

旧线上（`pearnly-app@ec2e5c95`，运行配置取证见 docs/work/cowork-backend/ocr-runtime-policy.md）：
发票为 economy 档，先用 `gemini-3.1-flash-lite` 直接读图，思考等级 MINIMAL；读出的结果过不了
确定性硬闸，才升到 `gemini-3.8-flash`（思考等级 LOW）重读（旧 `vertex.py:118-132`、
`direct_read.py:429-445`）。旧线上硬闸不过是改走 Vision 转写路线，但那条路线在线上缺凭据
不可用（同一份取证记录），所以这里直接升级到旧线上 L3 用的那个模型。

ERP 显式用这套，不跟站点的 `pearnly_ocr_mode`（DMS 的证件识别用企业档，与发票无关）。
"""
import frappe

from pearnly_erp_portal.recognition import checks

MODE = "economy"


def models():
    from pearnly_core import ocr

    table = ocr.MODE_MODEL_MAPS[MODE]
    return table["flash_lite"], table["fallback"]


TEXT_NOTE = ("The document is given below as the text layer extracted from its PDF instead of an image; "
             "read it the same way.\n\n")


def read(prompt, images, *, structured, review, label):
    """读一次；硬闸不过就用升级模型再读一次。

    PDF 自带文字层时首读只传文字（`inputs.text_path`，旧线上文字快路）；升级那次一律读原件。
    `structured(raw)` 把模型原始输出整理成结果（不合格返回 None）；`review(result)` 给出检查
    代码。返回 (结果, 检查代码, 用的模型)；两次都读不出结构返回 (None, [], 模型)。
    """
    from pearnly_core import ocr

    from pearnly_erp_portal.recognition import inputs

    primary, fallback = models()
    text = inputs.text_path(images)
    best = None
    for model in (primary, fallback):
        if text is not None and model == primary:
            raw = ocr.json_from_images(prompt + "\n" + TEXT_NOTE + text, [], model=model,
                                       thinking=ocr.thinking_for(model))
        else:
            raw = ocr.json_from_images(prompt, images, model=model, thinking=ocr.thinking_for(model))
        result = structured(raw) if isinstance(raw, dict) else None
        if result is None:
            frappe.logger("pearnly.ocr").info(f"{label}: {model} 没读出结构")
            continue
        issues = review(result)
        current = (result, issues, model)
        # 升级后仍不过：留硬闸问题少的那次（相同则留升级模型的）。
        if best is None or len(checks.hard(issues)) <= len(checks.hard(best[1])):
            best = current
        if not checks.hard(issues):
            break
        frappe.logger("pearnly.ocr").info(f"{label}: {model} 硬闸 {issues}，升级重读")
    return best or (None, [], fallback)


def describe(codes):
    from frappe import _

    return [_(checks.MESSAGES[code]) for code in codes if code in checks.MESSAGES]


def for_web(call):
    """网页端「上传识别」是同步请求：限流 / 容量不足 / 结果不确定给人话，不把内部异常抛到页面上。
    只给网页入口用 —— LINE 收单在队列里跑，`Deferred` 要原样抛出去让队列按时重试。"""
    from frappe import _
    from pearnly_core.execution.errors import Deferred, QueueFull, Uncertain

    try:
        return call()
    except (Deferred, QueueFull):
        frappe.throw(_("Recognition is busy right now. Please try again in a minute."))
    except Uncertain:
        frappe.throw(_("The recognition service did not answer in time. Please try again."))
