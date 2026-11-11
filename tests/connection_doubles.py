"""连接读取的严格替身：照真实接口的规矩收参数，不用 ``lambda *a, **k`` 什么都收（11-11 风险 7 真站点实测：
`push.mrerp.runner.release_kick` 传 ``lock=False`` 调 `connections.service.read_for_push`，真接口直接抛「先锁连接」，
离线替身却照收，缺陷一路漏到真站点）。

签名与真接口一致由 `test_cowork_mrerp_push.ManualEntries.test_the_strict_double_matches_the_real_read_for_push` 锁住。
"""


def read_for_push_returning(loaded, calls=None):
    """替 `pearnly_cowork.connections.service.read_for_push`：参数名、默认值与真接口相同；``lock`` 不是 True 就抛
    ``frappe.ValidationError``（真接口同一句），其余回 ``loaded``。``calls`` 给了就记下每次的参数。"""

    def read_for_push(connection_id, workspace, client=None, actor=None, lock=True):
        if lock is not True:
            import frappe

            raise frappe.ValidationError("Lock the connection before reading the push target.")
        if calls is not None:
            calls.append({"connection_id": connection_id, "workspace": workspace, "client": client, "actor": actor})
        return loaded

    return read_for_push
