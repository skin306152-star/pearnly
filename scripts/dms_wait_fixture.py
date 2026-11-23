"""DMS 离线测试用：LINE 等结果共用件（`pearnly_core.line_ui.receipt`）与 `pearnly_dms.line.out` 的「在等 / 结果存档」放进内存，
计时器换成不起线程的替身（不让真 55 秒计时在后面的用例里冒出兜底请求）。"""


class WaitStore:
    def __init__(self):
        self.values = {}

    def make_key(self, key):
        return key

    def set(self, key, value, ex=None, nx=False):
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    def get(self, key):
        return self.values.get(key)

    def getdel(self, key):
        return self.values.pop(key, None)

    def delete(self, key):
        self.values.pop(key, None)

    def sadd(self, key, value):
        self.values.setdefault(key, set()).add(value)

    def expire(self, key, seconds):
        return True


class IdleTimer:
    def __init__(self, seconds, function):
        self.seconds, self.function, self.daemon = seconds, function, False

    def start(self):
        pass


def install(enter):
    """``enter``：``TestCase.enterContext`` 或同样收一个上下文管理器的函数。"""
    from unittest.mock import patch

    from pearnly_core.line_ui import receipt
    from pearnly_dms.line import out

    import frappe

    try:
        frappe.session.user
    except (RuntimeError, AttributeError):
        enter(patch.object(frappe.local, "session", frappe._dict(user="Guest"), create=True))
    store = WaitStore()
    enter(patch.object(receipt, "_store", return_value=store))
    enter(patch.object(receipt, "Timer", IdleTimer))
    enter(patch.object(out, "_state", return_value=store))
    return store
