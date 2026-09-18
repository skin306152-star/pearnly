# -*- coding: utf-8 -*-
"""拆除验收的判据（宿主侧跑，数据用环境变量传进来）。

单独一个文件的原因和 `sample-dms-edit-state-check.py` 一样：要塞进 shell 里就得在
heredoc 里再套 heredoc，内层终止符会提前结束外层，脚本直接语法错。

  STEP=plan|verify   要跑哪一组判据
  PLAN_JSON / STATE_JSON  对应的数据
"""

import json
import os


def main() -> int:
    step = os.environ.get("STEP", "plan")
    ok = True

    def check(label, cond, detail=""):
        nonlocal ok
        ok = ok and bool(cond)
        print(("PASS " if cond else "FAIL ") + label + ((" — " + str(detail)) if detail else ""))

    if step == "plan":
        state = json.loads(os.environ["PLAN_JSON"])
        check("排出了拆除请求", bool(state.get("request")), state.get("request"))
        check("步骤数 = 站点 1 + 控制面清理 1", state.get("counts", {}).get("total") == 2,
              state.get("counts"))
        actions = [x["action"] for x in state.get("steps") or []]
        check("先拆站点、最后清理控制面", actions == ["drop_site", "purge_control"], actions)
    else:
        state = json.loads(os.environ["STATE_JSON"])
        check("控制面：租户行没了", not state["tenant_exists"])
        check("控制面：发号单没了", not state["issue_exists"])
        check("控制面：部署没了", not state["deployment_exists"])
        check("控制面：拆除请求自己也没了", state["requests_left"] == 0, state["requests_left"])
        check("控制面：钱包/身份绑定一类的记录也没了",
              state["wallet_left"] == 0 and state["identity_left"] == 0,
              {k: state[k] for k in ("wallet_left", "identity_left")})
        check("身份服务：账号已删除", not state["user_left"])
        check("身份服务：这个部署的 OIDC 客户端已删除", not state["client_left"])

    print("RESULT: " + ("ALL PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
