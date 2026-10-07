"""每分钟的定时入口：推进文件夹录入（没有待办文件时 `pump.run` 几条小查询就返回）。"""


def tick():
    from pearnly_cowork.folder_intake import pump

    pump.run()
