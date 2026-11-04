"""实操任务里上传的一份资料（子表，主会话 11-04 Q9）：原件编号、文件名、大小、识别任务号、上传时刻与页面发的请求键。
只记「这个任务里传过哪几份」，识别进度与结果照产品原来的识别记录现读（`express_operator.uploads`），这里不存状态。"""
from frappe.model.document import Document


class PearnlyExpressOperatorTaskUpload(Document):
    pass
