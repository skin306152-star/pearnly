from pearnly_cowork.connections import access
from pearnly_cowork.connections.controller import ConnectionDocument


class CoworkExpressAccountSet(ConnectionDocument):
    def validate(self):
        super().validate()
        # 规范键与规范根登记后永不改（ADR-021，方案 2.4 不变量）：账套锁按它们算，改了同一份物理账套就不再互斥。
        # 规范根只允许从空补上一次（补丁按推送记录 / 定时任务补的登记，由第一台够得到它的电脑补）。
        previous = self.get_doc_before_save()
        if previous and (self.account_key != previous.account_key or self.connection != previous.connection
                         or (previous.root_key and self.root_key != previous.root_key)):
            access.deny()
