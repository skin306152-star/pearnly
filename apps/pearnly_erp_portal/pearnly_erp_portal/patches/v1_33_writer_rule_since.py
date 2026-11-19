"""替写的旧件闸（主会话 11-17，与 Cowork 同一条）：存量站点迁移这一刻写上切换时刻（站点配置 ``pearnly_writer_rule_since``），之前排着的
写账件照旧只等提交人自己的电脑，不由别人的电脑悄悄写进 Express；之后排的按新规则（公司里有一台开着、够得到、登录人有权限的电脑就能写，
`cc_desktop.jobs` 领单时判）。新开户站点不跑这个补丁、没有这项配置 = 全部件按新规则。已有值不动（Cowork 的同名补丁先跑过也一样）。"""
from pearnly_core.cc_desktop import writer_rule


def execute():
    writer_rule.stamp_since()
