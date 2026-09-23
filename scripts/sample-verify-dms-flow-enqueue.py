# -*- coding: utf-8 -*-
"""后台任务入队验证：webhook 一拍即回，重活丢后台；这条链断了用户就是收不到卡。

为什么单独钉这一条：`_enqueue` 曾经把参数写成 `arg0/arg1/...`，看着像位置参数，而 frappe
的 worker 是 `method(**kwargs)`（`background_jobs.execute_job`）—— 于是**每个后台任务都
在 worker 里 TypeError**，webhook 这边却只看到"已入队"。症状是"发完照片一直没有回卡"，
不看 worker 日志根本查不出。所以这里钉四件事：

1. 守卫载荷的 kwargs **就是目标函数的形参名**；
2. 原生队列经过绑定版本守卫，再解析到目标函数；OCR 走共享持久任务入口；
3. 参数个数对不上时**当场炸**（不许留成 worker 里的静默失败）；
4. 静态扫本模块所有 `_enqueue(...)` 调用点，别再有对不上的。

用法: scripts/sample-verify-dms-flow-enqueue.sh <组> <站点>
"""


def main():
    from pathlib import Path
    import ast
    import inspect
    from unittest.mock import patch

    import frappe

    from pearnly_dms.intake import flow as dms_flow
    from pearnly_dms.line import out

    ok = []

    def ck(label, cond, detail=""):
        ok.append(bool(cond))
        print(("PASS " if cond else "FAIL ") + label + ((" — " + str(detail)) if detail else ""))

    captured = {}

    def fake_enqueue(method, **kwargs):
        captured["method"] = method
        captured["kwargs"] = kwargs

    identity = {"user": "operator@example.test", "binding_revision": "fixture-revision"}
    with patch.object(frappe, "enqueue", fake_enqueue), \
            patch("pearnly_dms.line.identity.current", return_value=identity), \
            patch.object(out, "start_loading") as loading:
        dms_flow._enqueue("probe", dms_flow.process_image,
                          {"user_id": identity["user"]}, "U", "MID", "REPLY")
        loading.assert_called_once_with("U", 60)

    # 原生队列控制参数不进入业务载荷；守卫原样展开 arguments 给目标函数。
    payload = captured.get("kwargs", {}).get("arguments") or {}
    ck("队列守卫和业务目标点号路径完整",
       captured.get("method") == "pearnly_dms.line.out.run_guarded"
       and captured.get("kwargs", {}).get("target") == "pearnly_dms.intake.flow.process_image")
    ck("入队的 kwargs 就是形参名（不是 arg0/arg1）",
       sorted(payload) == ["binding", "line_user_id", "message_id", "reply_token"]
       and not [k for k in payload if k.startswith("arg")],
       sorted(payload))
    ck("排队期间保留原绑定版本",
       payload.get("binding", {}).get("binding_revision") == identity["binding_revision"])
    ck("回复令牌随持久任务传递", payload.get("reply_token") == "REPLY")

    with patch("pearnly_dms.line.identity.matches", return_value=True), \
            patch.object(frappe, "get_attr", return_value=lambda **kw: kw) as resolve:
        ck("worker 守卫按具名参数交给业务函数",
           out.run_guarded(captured["kwargs"]["target"], payload) == payload)
        resolve.assert_called_once_with("pearnly_dms.intake.flow.process_image")
    with patch("pearnly_dms.line.identity.matches", return_value=False), \
            patch.object(frappe, "get_attr") as resolve:
        ck("解绑后的旧任务在解析业务函数前取消",
           out.run_guarded(captured["kwargs"]["target"], payload) == {"cancelled": "binding_changed"})
        resolve.assert_not_called()

    with patch("pearnly_dms.line.identity.current", return_value=identity), \
            patch.object(out, "start_loading"), \
            patch("pearnly_core.execution.work.submit", return_value="work-id") as submit, \
            patch.object(frappe, "enqueue") as enqueue:
        dms_flow._enqueue("image", dms_flow.process_image,
                          {"user_id": identity["user"]}, "U", "MID", "REPLY")
        submit.assert_called_once_with("dms.id_card", "MID", payload, identity["user"])
        enqueue.assert_not_called()
        ck("图片识别交给共享持久任务，使用消息幂等键", True)

    from pearnly_dms import hooks
    with patch("pearnly_dms.line.identity.matches", return_value=True), \
            patch.object(frappe, "get_installed_apps", return_value=["frappe", "pearnly_core", "pearnly_dms"]), \
            patch.object(dms_flow, "process_image", side_effect=lambda **kw: kw):
        handler = frappe.get_attr(hooks.pearnly_ocr_handlers["dms.id_card"])
        ck("真实注册消费者接收新任务并透传回复令牌", handler(**payload) == payload)
        legacy = {k: v for k, v in payload.items() if k != "reply_token"}
        ck("旧持久任务没有令牌仍可解析", handler(**legacy) == {**legacy, "reply_token": ""})

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

    src = Path(dms_flow.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    want_by_name = {}
    aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            want_by_name[node.name] = len([a for a in node.args.args if a.arg != "self"])
        if (isinstance(node, ast.Assign) and isinstance(node.value, ast.IfExp)
                and isinstance(node.value.body, ast.Name) and isinstance(node.value.orelse, ast.Name)):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    aliases[target.id] = [node.value.body.id, node.value.orelse.id]
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
        for candidate in aliases.get(name, [name]):
            want = want_by_name.get(candidate)
            if want != got:
                bad.append("%s: 给 %s 个，函数要 %s 个" % (candidate, got, want))
    ck("模块里每个 _enqueue 调用点的实参个数都对得上", not bad, bad)

    failed = [x for x in ok if not x]
    print("")
    print("合计: 通过 %d / 失败 %d" % (len(ok) - len(failed), len(failed)))
    print("RESULT: %s" % ("ALL PASS" if not failed else "FAIL"))
    if failed:
        raise AssertionError("Queue contract failed")


if __name__ == "__main__":
    main()
