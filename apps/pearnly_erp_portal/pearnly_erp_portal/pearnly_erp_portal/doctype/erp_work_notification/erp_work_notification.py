"""发给某个成员的看板提醒（被分派、被 @、关注的卡有评论 / 换列、快到期 / 过期）；写入只走 `work` 域。"""
from pearnly_erp_portal.work.documents import WorkDocument


class ERPWorkNotification(WorkDocument):
    pass
