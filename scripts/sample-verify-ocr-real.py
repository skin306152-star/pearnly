# -*- coding: utf-8 -*-
"""OCR 真样本验证：把**真图片**送进**真后端**，跟老产品的 ground truth 逐字段比。

由 `scripts/sample-verify-ocr-real.sh` 送进容器、用 `bench console` 跑。

跟 `sample-verify-ocr.py`（桩服务器，验"请求形状/升档顺序/门槛"）的分工：

| 脚本 | 验的是 |
| --- | --- |
| `sample-verify-ocr.py` | **代码这一层**：请求长什么样、两档怎么升、归一化门槛对不对 |
| 本脚本 | **模型认不认这张卡**：真图上真后端，跟真值逐字段比 |

样本来自老产品的评测料 `tests/eval/vision_ablation_p3/id_card/`（合成泰国身份证，
非真人数据，由 `generate.cjs` 固定种子可重现），真值是同目录的 ground_truth JSON。

三种结果必须分清（跟 `ocr_id_card.extract` 的契约一致）：

· 读出来了但字段不对 → `FAIL`，是模型/提示词的问题；
· **调不动模型**（`OcrUnavailable`：限流/配额/网络）→ `SKIP`，**不算通过也不算失败**，
  重新跑，绝不把基础设施问题记成"识别率"。

⚠️ 写法约束同 `sample-verify-ocr.py`：import 与状态都放 `main()` 里
（`bench console` 的 exec 命名空间与函数 globals 不是同一个）。
"""


def main():
    import json
    import os
    import re
    import time

    from pearnly_core import ocr
    from pearnly_dms import ocr_id_card

    SAMPLES = os.environ.get("PEARNLY_OCR_SAMPLES_DIR") or "/tmp/ocr-real"
    IMAGES = os.path.join(SAMPLES, "images")
    TRUTH = os.path.join(SAMPLES, "ground_truth")

    # 六个场景各取一（后面几个是"难看的图"：歪的、反光的、过期的、复印的）
    CASES = [
        "id_card_clean_001",
        "id_card_clean_002",
        "id_card_skewed_001",
        "id_card_glare_001",
        "id_card_expired_001",
        "id_card_photocopy_006",
    ]

    # 重试：实测连续跑多张会被 Vertex 限流（429）。配额问题要重试，不要记成"认不出"
    RETRIES = 2
    RETRY_WAIT_S = 25

    results = []

    def check(label, ok, detail=""):
        results.append((label, bool(ok), detail))
        print(("PASS " if ok else "FAIL ") + label + ((" — " + str(detail)) if detail else ""))

    def note(label, detail):
        print("  " + label + ": " + str(detail))

    def skip(label, detail):
        print("SKIP " + label + " — " + str(detail))

    def canon_date(v):
        """真值是 ISO（`2540-11-11`，佛历年），我们出 `dd/mm/yyyy`。两式归一到后者。"""
        s = str(v or "").strip()
        if s.upper() == "LIFETIME":
            return "LIFETIME"
        m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", s)
        if m:
            return "%s/%s/%s" % (m.group(3), m.group(2), m.group(1))
        m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", s)
        if m:
            return "%02d/%02d/%s" % (int(m.group(1)), int(m.group(2)), m.group(3))
        return s

    def fold(v):
        return re.sub(r"\s+", " ", str(v or "")).strip()

    def digits(v):
        return re.sub(r"\D", "", str(v or ""))

    # 后端起底先报出来 —— 这张图是谁认的（backend / 档 / 模型 / 区域）要写在结论里
    st = ocr.api_status()
    note("后端", "%s / %s / %s" % (st.get("backend"), st.get("mode"), st.get("models")))
    note("区域", st.get("locations"))
    if not st.get("configured"):
        check("OCR 已配置", False, "站点没配凭据，真调不起来")
        print("RESULT: FAIL（没配凭据）")
        return

    passed = failed = skipped = 0
    for case in CASES:
        img_path = os.path.join(IMAGES, case + ".jpg")
        gt_path = os.path.join(TRUTH, case + ".json")
        if not (os.path.exists(img_path) and os.path.exists(gt_path)):
            skip(case, "缺图或缺真值（%s）" % img_path)
            skipped += 1
            continue

        with open(img_path, "rb") as fh:
            img = fh.read()
        with open(gt_path, "r", encoding="utf-8") as fh:
            gt = json.load(fh)

        out = None
        unavailable = ""
        for attempt in range(RETRIES + 1):
            try:
                out = ocr_id_card.extract(img)
                break
            except ocr.OcrUnavailable as exc:
                unavailable = str(exc)[:200]
                if attempt < RETRIES:
                    note(case, "调不动模型，%ds 后重试（第 %d 次）" % (RETRY_WAIT_S, attempt + 1))
                    time.sleep(RETRY_WAIT_S)
            except ocr_id_card.IdCardExtractError as exc:
                # 图的问题：两档都没读出内容
                check(case, False, "两档都没读出内容：%s" % str(exc)[:160])
                failed += 1
                break

        if out is None:
            if unavailable:
                skip(case, "配额/网络调不动模型（**不是**识别结果）：%s" % unavailable)
                skipped += 1
            continue
        if not isinstance(out, dict):
            continue

        got = out.get("id_card") or {}
        got_addr = got.get("address") or {}
        gt_addr = gt.get("address") or {}

        fields = {
            "people_id": (digits(got.get("people_id")), digits(gt.get("people_id"))),
            "first_name": (fold(got.get("first_name")), fold(gt.get("first_name"))),
            "last_name": (fold(got.get("last_name")), fold(gt.get("last_name"))),
            "birthday_be": (canon_date(got.get("birthday_be")), canon_date(gt.get("birthday_be"))),
            "expiry_date_be": (canon_date(got.get("expiry_date_be")), canon_date(gt.get("expiry_date_be"))),
            "prefix_name": (fold(got.get("prefix_name")), fold(gt.get("prefix_name"))),
            "address.subdistrict": (fold(got_addr.get("subdistrict")), fold(gt_addr.get("subdistrict"))),
            "address.province": (fold(got_addr.get("province")), fold(gt_addr.get("province"))),
            "address.zipcode": (digits(got_addr.get("zipcode")), digits(gt_addr.get("zipcode"))),
        }
        wrong = {k: v for k, v in fields.items() if v[0] != v[1]}
        # 命门：号 + 名 + 姓。这三个错了 = 建到别人名下，跟老产品打分器同一条线
        critical = [k for k in ("people_id", "first_name", "last_name") if k in wrong]

        detail = "；".join("%s: 读到「%s」真值「%s」" % (k, v[0], v[1]) for k, v in wrong.items())
        if critical:
            check(case, False, "命门字段错 → " + detail)
            failed += 1
        elif wrong:
            print("PART " + case + " — 命门对，另几项有差：" + detail)
            passed += 1
        else:
            check(case, True, "全字段一致")
            passed += 1

        if out.get("needs_review"):
            note(case, "needs_review=True missing=%s" % out.get("missing_fields"))
        note(case, "置信=%s" % out.get("confidence"))

    print("")
    note("合计", "通过 %d / 失败 %d / 跳过 %d" % (passed, failed, skipped))
    if failed:
        print("RESULT: FAIL")
    elif skipped and not passed:
        print("RESULT: SKIP（调不动模型，没验成）")
    elif skipped:
        print("RESULT: PARTIAL（有 %d 张因配额跳过）" % skipped)
    else:
        print("RESULT: ALL PASS")


main()
