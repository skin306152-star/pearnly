"""每分钟的定时入口：站点总闸没开就立即返回（只读一次站点配置，不查库、不导入推进代码）。"""
from pearnly_cowork.folder_intake import settings


def tick():
    if settings.enabled():
        from pearnly_cowork.folder_intake import pump

        pump.run()
