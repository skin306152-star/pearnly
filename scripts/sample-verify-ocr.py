# -*- coding: utf-8 -*-
"""OCR 自检：请求形状 + 两档升级 + 归一化门槛。

由 `scripts/sample-verify-ocr.sh` 送进容器、用 `bench console` 跑。

**为什么用桩服务器而不是直接打真 API**：真调用需要可用凭据，而本机两样都没有
（AI Studio 那把 key 的预付额度已耗尽 → 429；生产的 Vertex 项目当前账号无权访问）。
桩服务器能验的是**这一层代码自己的问题**：请求长什么样、两档是不是按顺序升、
归一化门槛对不对、密钥有没有泄露到日志。真正的"模型认不认这张卡"必须用真凭据
+ 真样本单独验，这一层**不冒充**那个结论。

⚠️ 写法约束同 `sample-verify-foundation.py`：import 与状态都放 `main()` 里
（`bench console` 的 exec 命名空间与函数 globals 不是同一个）。
"""


def main():
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    import frappe

    from pearnly_core import ocr
    from pearnly_dms import ocr_id_card

    results = []
    captured = []

    def check(label, ok, detail=""):
        results.append((label, bool(ok), detail))
        print(("PASS " if ok else "FAIL ") + label + ((" — " + str(detail)) if detail else ""))

    # ── 桩服务器：第一次故意给不合格的号，逼出"升档" ──────────────────────
    BAD = {"people_id": "123", "first_name_th": "สมชาย", "last_name_th": "ใจดี"}
    GOOD = {
        "people_id": "1 2345 67890 12 3",
        "prefix": "น.ส.",
        "first_name_th": "สมหญิง",
        "last_name_th": "แสนสุข",
        "date_of_birth_be": "01/04/2533",
        # 提示词明确要求"卡面写 ตลอดชีพ 时返回字面量 LIFETIME"，所以桩按契约给 LIFETIME
        "expiry_date_be": "LIFETIME",
        "address_raw": "99 หมู่ 5 ต.บางรัก อ.เมือง จ.กรุงเทพมหานคร 10500",
        "house_no": "99",
        "moo": "5",
        "subdistrict": "บางรัก",
        "district": "เมือง",
        "province": "กรุงเทพมหานคร",
        "zipcode": "10500",
    }

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                body = {}
            model = self.path.split("/models/")[-1].split(":")[0]
            captured.append({
                "model": model,
                "body": body,
                "key": self.headers.get("x-goog-api-key") or "",
            })
            # 第 1 次给不合格的号（触发升档），第 2 次给合格的号
            payload = BAD if len(captured) == 1 else GOOD
            out = json.dumps({
                "candidates": [{"content": {"parts": [
                    {"text": json.dumps(payload, ensure_ascii=False)}
                ]}}]
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    # 只在本进程里改 conf，**不写站点配置**（不污染站点、也不用改回来）
    saved_key = frappe.conf.get("pearnly_ocr_api_key")
    saved_endpoint = frappe.conf.get("pearnly_ocr_endpoint")
    saved_mode = frappe.conf.get("pearnly_ocr_mode")
    frappe.conf["pearnly_ocr_api_key"] = "stub-key-not-real"
    frappe.conf["pearnly_ocr_endpoint"] = (
        f"http://127.0.0.1:{port}/v1beta/models/{{model}}:generateContent"
    )
    frappe.conf["pearnly_ocr_mode"] = "enterprise"

    try:
        # 一张最小的合法 PNG（1x1），只为拿到 mime 判定
        png = bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
            "890000000a49444154789c6360000002000154a24f5c0000000049454e44ae426082"
        )

        # ── 1. A 档（enterprise）：两臂是同一个模型，**只该调一次** ──────────
        # 依据：老产品 `models_with_fallback` 会去重（`if fb not in out`），
        # 所以两臂同模型时"升档"没有意义，不该白发第二次请求。
        frappe.conf["pearnly_ocr_mode"] = "enterprise"
        captured.clear()
        out_enterprise = ocr_id_card.extract(png)
        check("A 档两臂同模型 → 只调一次（不白发请求）", len(captured) == 1,
              [c["model"] for c in captured])
        check("A 档用的是 gemini-3.8-flash",
              captured and captured[0]["model"] == "gemini-3.8-flash",
              [c["model"] for c in captured])
        check("读出不合格的号 → 标 needs_review（不硬推）",
              out_enterprise.get("needs_review") is True,
              out_enterprise.get("missing_fields"))

        # ── 2. B 档（economy）：读取臂与升级臂不同 → 该升档一次 ──────────────
        frappe.conf["pearnly_ocr_mode"] = "economy"
        captured.clear()
        out = ocr_id_card.extract(png)
        check("B 档升档一次（共两次调用）", len(captured) == 2, [c["model"] for c in captured])
        check("B 档先走轻量臂、再走升级臂",
              [c["model"] for c in captured] == ["gemini-3.1-flash-lite", "gemini-3.8-flash"],
              [c["model"] for c in captured])

        # ── 3. 请求形状：提示词、图片、生成参数 ────────────────────────────
        body = captured[0]["body"] if captured else {}
        parts = ((body.get("contents") or [{}])[0].get("parts") or [])
        prompt = parts[0].get("text", "") if parts else ""
        check("提示词带上了（泰文身份证 + 严格 JSON 要求）",
              "THAI NATIONAL ID CARD" in prompt and "STRICT JSON" in prompt)
        check("提示词保留了那句泰文月份陷阱说明（เม.ย. = 04 不是 05）",
              "เม.ย. is month 04 (April), NOT 05" in prompt)
        check("提示词保留了『不要编 13 位号』", "Do NOT invent a 13-digit number" in prompt)
        check("图片按 inline_data + 正确 mime 传",
              len(parts) > 1
              and parts[1].get("inline_data", {}).get("mime_type") == "image/png"
              and bool(parts[1].get("inline_data", {}).get("data")))
        cfg = body.get("generationConfig") or {}
        check("temperature=0（可复现优先）", cfg.get("temperature") == 0, cfg.get("temperature"))
        check("要求模型直接吐 JSON", cfg.get("responseMimeType") == "application/json")
        check("密钥走请求头，不在 body 里",
              captured and captured[0]["key"] == "stub-key-not-real"
              and "stub-key-not-real" not in json.dumps(body))

        # ── 3. 归一化：升级后拿到的是合格号 ────────────────────────────────
        card = out.get("id_card") or {}
        check("升级后拿到 13 位号（去掉空格/横线）",
              card.get("people_id") == "1234567890123", card.get("people_id"))
        check("姓名抽到了", card.get("first_name") == "สมหญิง" and card.get("last_name") == "แสนสุข")
        check("称谓缩写归一（น.ส. → นางสาว）", card.get("prefix_name") == "นางสาว",
              card.get("prefix_name"))
        check("出生日期归一到佛历（2533 已是佛历，不重复 +543）",
              card.get("birthday_be") == "01/04/2533", card.get("birthday_be"))
        check("终身证印归一成 LIFETIME", card.get("expiry_date_be") == "LIFETIME",
              card.get("expiry_date_be"))
        check("地址各字段落下来了",
              (card.get("address") or {}).get("province") == "กรุงเทพมหานคร"
              and (card.get("address") or {}).get("zipcode") == "10500")
        check("这条不需要人工核对", out.get("needs_review") is False, out.get("missing_fields"))

        # ── 4. 门槛（纯函数，不依赖网络）──────────────────────────────────
        bad = ocr_id_card.normalize({"people_id": "123", "first_name_th": "A",
                                     "last_name_th": "B"})
        check("非 13 位的号一律置空（绝不推给 DMS）", bad["id_card"]["people_id"] == "")
        check("缺号 → needs_review", bad["needs_review"] is True, bad["missing_fields"])

        greg = ocr_id_card.normalize({"people_id": "1234567890123", "first_name_th": "A",
                                      "last_name_th": "B",
                                      "date_of_birth_be": "01/04/1990"})
        check("公历年自动 +543（1990 → 2533）",
              greg["id_card"]["birthday_be"] == "01/04/2533", greg["id_card"]["birthday_be"])

        # 一条已知的边界，显式记下来（照搬老产品的行为，不是 bug 也不是我改的）：
        # 只有模型**按提示词返回字面量 LIFETIME** 才会被认成终身证；如果它返回泰文
        # ตลอดชีพ，就会被当成"没有可解析的日期"而落空串。终身证在老年人里很常见，
        # 这一条值得以后单独定：是要放宽成两种写法都认，还是维持现状。
        thai_word = ocr_id_card.normalize({"people_id": "1234567890123", "first_name_th": "A",
                                           "last_name_th": "B", "expiry_date_be": "ตลอดชีพ"})
        check("（已知边界）模型若返回泰文 ตลอดชีพ，到期日会落空串 —— 与老产品一致",
              thai_word["id_card"]["expiry_date_be"] == "",
              thai_word["id_card"]["expiry_date_be"])

        # ── 5. 状态接口要诚实 ─────────────────────────────────────────────
        frappe.conf["pearnly_ocr_api_key"] = ""
        check("没配密钥时状态就是『没配』（不假装可用）",
              ocr.api_status()["configured"] is False)
        selftest = ocr.api_selftest()
        check("没配密钥时自检返回 no_key", selftest.get("reason") == "no_key", selftest)

    finally:
        server.shutdown()
        for k, v in (("pearnly_ocr_api_key", saved_key),
                     ("pearnly_ocr_endpoint", saved_endpoint),
                     ("pearnly_ocr_mode", saved_mode)):
            if v is None:
                frappe.conf.pop(k, None)
            else:
                frappe.conf[k] = v

    return results


try:
    _results = main()
except Exception:
    import traceback

    traceback.print_exc()
    _results = [("自检脚本自身跑通（没有抛异常）", False, "见上面的 traceback")]

_failed = [r for r in _results if not r[1]]
print("")
if not _results:
    print("RESULT: SKIP")
elif _failed:
    print(f"RESULT: FAIL ({len(_failed)}/{len(_results)})")
    for _label, _ok, _detail in _failed:
        print("  - " + _label + ((" — " + str(_detail)) if _detail else ""))
else:
    print(f"RESULT: ALL PASS ({len(_results)})")
