// 「LINE 开通手册」：新租户在 LINE 后台与开户单上要做的每一步（用户 2026-09-30）。
// 正文由服务端给（`pearnly_control.onboarding.line_guide`），这里只负责按域名前缀填地址与复制。
frappe.pages["pearnly-line-guide"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("LINE Setup Guide"),
		single_column: true,
	});
	const body = $('<div class="pearnly-line-guide"></div>').appendTo(page.main);
	const urls = (prefix) => {
		// 云端开户目标的基础域名（本机样板目标是 localhost，不用它）。
		const cloud = Object.values(frappe.boot.pearnly_target_domains || {})
			.find((d) => d && !String(d).endsWith("localhost"));
		const domain = (prefix || "acme") + "." + (cloud || "pearnly.com");
		return {
			prefix: prefix || "acme",
			webhook: "https://" + domain + "/api/method/pearnly_core.line.webhook",
			// 站点根：LIFF 只要求页面在落地地址之下，填根最稳（开户时会核对，见 site-step.check_liff）。
			"liff-app": "https://" + domain + "/",
			"liff-dms": "https://" + domain + "/liff/dms-booking",
		};
	};
	const clean = (value) => String(value || "").trim().toLowerCase().replace(/[^a-z0-9-]/g, "");
	const fill = () => {
		const values = urls(clean(prefix.get_value()));
		body.find("[data-fill]").each(function () {
			$(this).text(values[this.dataset.fill] || "");
		});
	};
	const prefix = page.add_field({
		fieldname: "prefix",
		label: __("Domain Prefix"),
		fieldtype: "Data",
		change: fill,
	});
	frappe.xcall("pearnly_control.onboarding.line_guide.content", {}, "GET").then((r) => {
		body.html(r.html);
		// 每个生成的地址后面放一个原生小按钮，点了复制。
		body.find("code[data-fill]").each(function () {
			const key = this.dataset.fill;
			$('<button type="button" class="btn btn-default btn-xs ml-2"></button>')
				.text(__("Copy"))
				.on("click", () => frappe.utils.copy_to_clipboard(urls(clean(prefix.get_value()))[key]))
				.insertAfter(this);
		});
		fill();
	});
};
