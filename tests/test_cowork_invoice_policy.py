import unittest

from pearnly_cowork.ocr.policy import invoice_policy, model_for, resolve_mode, vertex_location


class InvoicePolicyTests(unittest.TestCase):
    def test_current_production_selection_overrides_stale_environment_models(self):
        env = {"OCR_LLM_BACKEND": "vertex", "OCR_FLASHLITE_MODEL": "gemini-3.5-flash",
               "OCR_FALLBACK_MODEL": "gemini-3.5-flash", "OCR_FAST_PATH_ENABLED": "true", "OCR_FEWSHOT_ENABLED": "1"}
        p = invoice_policy(env, {"mode": "economy", "overrides_by_task": {"invoice": "economy"}})
        self.assertEqual((p.mode, p.backend, p.first_model, p.fallback_model),
                         ("economy", "vertex", "gemini-3.1-flash-lite", "gemini-3.8-flash"))
        self.assertEqual((p.first_location, p.fallback_location, p.l3_location), ("global", "global", "global"))
        self.assertEqual(p.l3_model, "gemini-3.8-flash")
        self.assertEqual([p.route(n) for n in (1, 20, 21, 50)], ["image_direct"] * 4)  # ADR-013：逐页复核，直读到 50 页
        self.assertEqual(invoice_policy({**env, "OCR_IMAGE_DIRECT_MAX_PAGES": "20"}, {"mode": "economy"}).route(21),
                         "pipeline_v1")
        self.assertTrue(p.fewshot_enabled)
        self.assertTrue(p.text_fast_path)
        self.assertFalse(p.double_read)
        self.assertEqual(p.route(1, text_path_accepted=True), "pipeline_v1")

    def test_priority_and_no_mutation(self):
        cfg = {"mode": "enterprise", "overrides_by_task": {"invoice": "qwen"}}
        self.assertEqual(resolve_mode({}, cfg), "qwen")
        self.assertEqual(resolve_mode({"OCR_ENGINE_MODE": "direct35"}, cfg), "direct35")
        self.assertEqual(resolve_mode({"OCR_ENGINE_MODE": "auto"}, cfg), "qwen")
        self.assertEqual(resolve_mode({"OCR_ENGINE_MODE": "garbage"}, cfg), "qwen")
        self.assertEqual(cfg, {"mode": "enterprise", "overrides_by_task": {"invoice": "qwen"}})

    def test_auto_plan_exemption_and_shallow_replacement(self):
        cfg = {"mode": "auto", "defaults_by_plan": {"S": "enterprise", "exempt": "qwen"}}
        self.assertEqual(resolve_mode({}, cfg, plan_code="S"), "enterprise")
        self.assertEqual(resolve_mode({}, cfg, plan_code="L"), "economy")
        self.assertEqual(resolve_mode({}, cfg, plan_code="S", is_exempt=True), "qwen")
        self.assertEqual(resolve_mode({}, {"mode": "auto", "defaults_by_plan": []}), "economy")
        self.assertEqual(resolve_mode({}, {"mode": "bad", "enabled": False}), "economy")

    def test_non_gemini_modes_not_mislabelled_as_gemini(self):
        for mode in ("qwen", "selfhost"):
            p = invoice_policy({"OCR_ENGINE_MODE": mode, "OCR_LLM_BACKEND": "vertex"})
            self.assertEqual(p.backend, mode)
            self.assertEqual((p.first_model, p.fallback_model, p.l3_model), ("", "", ""))
        self.assertEqual(invoice_policy({"OCR_ENGINE_MODE": "qwen"}).route(1), "qwen_direct")
        self.assertEqual(invoice_policy({"OCR_ENGINE_MODE": "enterprise", "OCR_LLM_BACKEND": "qwen"}).backend, "vertex")

    def test_empty_fallback_disables_only_unoverridden_model(self):
        p = invoice_policy({"OCR_ENGINE_MODE": "direct35", "OCR_FALLBACK_MODEL": "", "OCR_LLM_BACKEND": "vertex"})
        self.assertEqual(p.fallback_model, "")
        self.assertIsNone(p.fallback_location)
        self.assertEqual((p.l3_model, p.l3_location), ("gemini-3.5-flash", "asia-southeast1"))
        self.assertEqual(model_for("direct35", "fallback", {"OCR_FALLBACK_MODEL": ""}), "")
        self.assertEqual(model_for("economy", "fallback", {"OCR_FALLBACK_MODEL": ""}), "gemini-3.8-flash")
        self.assertEqual(model_for("direct35", "escalate", {"OCR_FALLBACK_MODEL": ""}), "")
        self.assertEqual(model_for("direct35", "flash_lite", {"OCR_FLASHLITE_MODEL": ""}), "gemini-3.5-flash")

    def test_region_override_does_not_move_frozen_38_model(self):
        env = {"VERTEX_LOCATION_25": "us-central1", "VERTEX_LOCATION": "asia-southeast1"}
        self.assertEqual(vertex_location("gemini-3.8-flash", env), "global")
        self.assertEqual(vertex_location("gemini-3.1-flash-lite", env), "us-central1")
        self.assertEqual(vertex_location("gemini-3.5-flash", env), "asia-southeast1")

    def test_switch_semantics_and_missing_page_rejected(self):
        self.assertEqual(invoice_policy({"OCR_DIRECT_TIMEOUT_SECONDS": ""}).direct_timeout_seconds, 60)
        p = invoice_policy({"OCR_IMAGE_DIRECT": "off", "OCR_FAST_PATH_ENABLED": "1", "OCR_FEWSHOT_ENABLED": " 1 "})
        self.assertEqual(p.route(1), "pipeline_v1")
        self.assertFalse(p.text_fast_path)
        self.assertFalse(p.fewshot_enabled)
        for n in (0, -1, True, 1.5):
            with self.assertRaises(ValueError):
                p.route(n)


if __name__ == "__main__":
    unittest.main()
