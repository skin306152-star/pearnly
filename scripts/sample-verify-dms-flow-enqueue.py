# -*- coding: utf-8 -*-
"""后台任务入队验证：webhook 一拍即回，重活丢后台；这条链断了用户就是收不到卡。

为什么单独钉这一条：`_enqueue` 曾经把参数写成 `arg0/arg1/...`，看着像位置参数，而 frappe
的 worker 是 `method(**kwargs)`（`background_jobs.execute_job`）—— 于是**每个后台任务都
在 worker 里 TypeError**，webhook 这边却只看到"已入队"。症状是"发完照片一直没有回卡"，
不看 worker 日志根本查不出。所以这里钉四件事：

1. 入队的 kwargs **就是目标函数的形参名**；
2. 点号路径能解析回同一个函数；
3. 参数个数对不上时**当场炸**（不许留成 worker 里的静默失败）；
4. 静态扫本模块所有 `_enqueue(...)` 调用点，别再有对不上的。

用法: scripts/sample-verify-dms-flow-enqueue.sh <组> <站点>
"""


def main():
    import ast
    import inspect

    import frappe

    from pearnly_dms import dms_flow

    ok = []

    def ck(label, cond, detail=""):
        ok.append(bool(cond))
        print(("PASS " if cond else "FAIL ") + label + ((" — " + str(detail)) if detail else ""))

    captured = {}

    def fake_enqueue(method, **kwargs):
        captured["method"] = method
        captured["kwargs"] = kwargs

    real_enqueue = frappe.enqueue
    frappe.enqueue = fake_enqueue
    try:
        dms_flow._enqueue("probe", dms_flow.process_image,
                          {"line_user_id": "U"}, "U", "MID")
    finally:
        frappe.enqueue = real_enqueue

    # frappe.enqueue 的控制参数（queue/timeout/job_id）不是目标函数的参数，先剥掉
    payload = {
        key: value
        for key, value in (captured.get("kwargs") or {}).items()
        if key in inspect.signature(dms_flow.process_image).parameters
    }
    ck("入队用的点号路径指到同一个函数",
       captured.get("method") == "pearnly_dms.dms_flow.process_image", captured.get("method"))
    ck("入队的 kwargs 就是形参名（不是 arg0/arg1）",
       sorted(payload) == ["binding", "line_user_id", "message_id"]
       and not [k for k in (captured.get("kwargs") or {}) if k.startswith("arg")],
       sorted(payload))

    try:
        # worker 就是这么调的：`method(**kwargs)`（frappe execute_job 最后一行）
        inspect.signature(dms_flow.process_image).bind(**payload)
        ck("worker 按形参名调用能落地（不会 TypeError）", True)
    except TypeError as exc:
        ck("worker 按形参名调用能落地（不会 TypeError）", False, exc)

    try:
        dms_flow._enqueue("probe", dms_flow.process_image, "少了参数")
        ck("参数个数对不上时当场炸（不留成 worker 里的静默失败）", False, "没炸")
    except TypeError as exc:
        ck("参数个数对不上时当场炸（不留成 worker 里的静默失败）", True, str(exc)[:40])

    src = open(dms_flow.__file__, encoding="utf-8").read()
    tree = ast.parse(src)
    want_by_name = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            want_by_name[node.name] = len([a for a in node.args.args if a.arg != "self"])
    bad = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not (isinstance(fn, ast.Name) and fn.id == "_enqueue"):
            continue
        if len(node.args) < 2:
            continue
        target = node.args[1]
        name = getattr(target, "id", None) or getattr(target, "attr", None)
        got = len(node.args) - 2
        want = want_by_name.get(name)
        if want != got:
            bad.append("%s: 给 %s 个，函数要 %s 个" % (name, got, want))
    ck("模块里每个 _enqueue 调用点的实参个数都对得上", not bad, bad)

    failed = [x for x in ok if not x]
    print("")
    print("合计: 通过 %d / 失败 %d" % (len(ok) - len(failed), len(failed)))
    print("RESULT: %s" % ("ALL PASS" if not failed else "FAIL"))


if __name__ == "__main__":
    main()
