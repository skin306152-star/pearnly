# -*- coding: utf-8 -*-
"""页面点完保存之后，回读服务端状态并对照（`sample-verify-dms-edit-page.sh` 的第三步）。

为什么单独一个文件：这段判据要塞进 shell 脚本里的话，就得在 shell 的 heredoc 里再套一个
heredoc —— 内层终止符会提前结束外层，脚本直接语法错（真踩过）。数据走环境变量：

  MODE        booking | customer
  OLD_NONCE   保存前的 nonce
  STATE_JSON  `sample-dms-edit-fixture.py inspect` 打出来的那行 JSON
"""

import json
import os


def main() -> int:
    mode = os.environ.get("MODE", "booking")
    old = os.environ.get("OLD_NONCE", "")
    state = json.loads(os.environ["STATE_JSON"])
    ok = True

    def check(label, cond, detail=""):
        nonlocal ok
        ok = ok and bool(cond)
        print(("PASS " if cond else "FAIL ") + label + ((" — " + str(detail)) if detail else ""))

    check("保存换了新 nonce（旧卡作废）", state["nonce"] and state["nonce"] != old, state["nonce"])
    check("页面改的那一格真的进了草稿", state["phone"] == "0866666666", state["phone"])
    check("新版预览卡真的发出去了（本机镜像）", state["card_type"] == "flex", state["card_type"])
    check(
        "发出去的卡带的是**新** nonce",
        any(state["nonce"] in action for action in state["card_actions"])
        and not any(old in action for action in state["card_actions"]),
        state["card_actions"],
    )
    if mode == "customer":
        # 客户档那一态是 `reviewing`：保存只改草稿、**不写 DMS**（写入仍要用户在 LINE 里点确认）
        check("客户档保存后会话态是 reviewing", state["state"] == "reviewing", state["state"])
    else:
        check(
            "会话态还是 booking_review（可继续确认）",
            state["state"] == "booking_review",
            state["state"],
        )
        check("付款金额原样保留在草稿里", state["amount"] == "5000.00", state["amount"])
    print("RESULT: " + ("ALL PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
