"""替写的旧件闸（主会话 11-17）：存量站点迁移这一刻写上切换时刻（站点配置 ``pearnly_writer_rule_since``），之前排着的件照旧只等
提交人自己的电脑，不由别人的电脑悄悄写进 Express；之后排的件按新规则（公司里有一台开着、够得到、登录人有权限的电脑就能写）。
新开户站点不跑这个补丁、没有这项配置 = 全部件按新规则。已有值不动（ERP 的同名补丁先跑过也一样）。"""
from pearnly_core.cc_desktop import writer_rule


def execute():
    writer_rule.stamp_since()
