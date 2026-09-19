(() => {
	const p = frappe.pearnly;
	p.home = async (page, t, wrapper) => {
		let home,
			form,
			table,
			timer,
			busy = false;
		let tab = "usage",
			period = "all",
			recordRequest = 0;
		const money = (value) => format_currency(Number(value || 0), "THB");
		const read = (name, label, value, fieldtype = "Data") => ({
			fieldname: name,
			label: t(label),
			fieldtype,
			read_only: 1,
			default: value,
			options: fieldtype === "Currency" ? "THB" : undefined,
		});
		async function refresh() {
			if (busy || !$(wrapper).is(":visible")) return;
			busy = true;
			try {
				home = await p.call("pearnly_core.billing_www.home");
				table?.destroy();
				page.main.empty();
				const sub = home.subscription;
				if (home.can_manage) page.set_primary_action(t("home.topup"), topup);
				else page.clear_primary_action();
				page.clear_inner_toolbar();
				if (home.can_manage) page.add_inner_button(t("home.plans.title"), subscription);
				page.add_inner_button(t("home.ledger.export"), () =>
					location.assign("/api/method/pearnly_core.billing_www.export_records"),
				);
				page.set_title_sub(
					home.pending_topups.length
						? p.escape(
								t("home.pending.count", home.pending_topups.length) +
									" " +
									t("home.pending.hint"),
							)
						: "",
					"orange",
				);
				form = p.layout(page.main, [
					{ fieldtype: "Section Break", label: t("home.balance.title") },
					read("balance", "home.balance.title", money(home.balance_thb)),
					{ fieldtype: "Column Break" },
					read(
						"plan",
						"home.plan.current",
						sub ? t("home.plan.code") + " " + sub.plan_code : t("home.plan.none"),
					),
					{ fieldtype: "Column Break" },
					read("used", "home.col.pages", home.pages_used_this_month, "Int"),
					{ fieldtype: "Section Break" },
					{
						fieldtype: "HTML",
						options: sub
							? `<p>${p.escape(t("home.plan.usage", sub.pages_used_this_cycle, sub.quota, sub.remaining))}</p><p class="text-muted">${p.escape(t("home.plan.over", money(sub.over_rate), frappe.datetime.str_to_user(sub.cycle_end)))}</p><p class="text-muted">${p.escape(t("home.plan.cycleNote"))}</p>`
							: `<p class="text-muted">${p.escape(t("home.plan.noneHint"))}</p>`,
					},
					{ fieldtype: "Section Break", label: t("home.plans.title") },
					{ fieldtype: "HTML", fieldname: "plans" },
					{ fieldtype: "Section Break", label: t("home.ledger.title") },
					{
						fieldtype: "Select",
						fieldname: "tab",
						label: __("Type"),
						default: tab,
						options: [
							{ value: "usage", label: t("home.ledger.usage") },
							...(home.can_manage
								? [{ value: "topup", label: t("home.topup") }]
								: []),
							{ value: "ocr", label: t("home.ledger.ocr") },
						],
						change: () => {
							tab = form.get_value("tab");
							void records();
						},
					},
					{ fieldtype: "Column Break" },
					{
						fieldtype: "Select",
						fieldname: "period",
						label: __("Period"),
						default: period,
						options: ["all", "day", "month", "year"].map((value, i) => ({
							value,
							label: t(
								[
									"home.period.all",
									"home.period.today",
									"home.period.month",
									"home.period.year",
								][i],
							),
						})),
						change: () => {
							period = form.get_value("period");
							void records();
						},
					},
					{ fieldtype: "Section Break" },
					{ fieldtype: "HTML", fieldname: "records" },
				]);
				const fields = [];
				for (const [i, plan] of home.plans.entries()) {
					if (i) fields.push({ fieldtype: "Column Break" });
					fields.push({
						fieldtype: "HTML",
						options: `<h5>${p.escape(t("home.plan.code"))} ${p.escape(plan.code)}</h5><p>${p.escape(money(plan.fee))} · ${p.escape(t("home.plans.perCycle", plan.cycle_days))}</p><p class="text-muted">${p.escape(t("home.plans.quota", plan.quota))}<br>${p.escape(t("home.plans.over", money(plan.over_rate)))}</p>`,
					});
					if (home.can_manage)
						fields.push({
							fieldtype: "Button",
							label:
								sub?.plan_code === plan.code
									? t("home.plan.current")
									: t("home.plan.subscribe"),
							click: () => subscription(plan.code),
						});
				}
				const plans = p.layout(form.fields_dict.plans.$wrapper, fields);
				plans.wrapper.find(".form-column").wrapInner('<div class="pearnly-plan"></div>');
				await records();
			} finally {
				busy = false;
			}
		}
		async function records() {
			if (!form?.fields_dict.records) return;
			const request = ++recordRequest;
			const data = await p.call("pearnly_core.billing_www.records", { tab, period });
			if (request !== recordRequest) return;
			const status = (code) =>
				t(
					{
						pending: "home.status.pending",
						approved: "home.status.credited",
						rejected: "home.status.rejected",
						ok: "home.status.ok",
						needs_review: "home.status.toreview",
						failed: "home.status.failed",
					}[code] || code,
				);
			let cols, rows;
			if (tab === "topup") {
				cols = ["time", "reference", "status", "requested", "received", "note"];
				rows = data.rows.map((r) => [
					r.creation,
					r.name,
					status(r.status),
					money(r.requested_thb),
					r.approved_thb ? money(r.approved_thb) : "—",
					r.review_note || r.note || "—",
				]);
			} else if (tab === "ocr") {
				cols = ["time", "result", "file", "charges", "summary"];
				rows = data.rows.map((r) => [
					r.creation,
					status(r.status),
					r.filename || "—",
					r.charged_thb == null ? "—" : money(r.charged_thb),
					r.error || r.detail || "—",
				]);
			} else {
				cols = ["time", "product", "pages", "amount", "balanceAfter", "description"];
				rows = data.rows.map((r) => [
					r.creation,
					r.product,
					r.pages || 0,
					money(r.amount_thb),
					money(r.balance_after),
					r.description,
				]);
			}
			table?.destroy();
			table = await p.table(
				form.fields_dict.records.$wrapper,
				cols.map((k) => t("home.col." + k)),
				rows,
			);
		}
		function subscription(code) {
			const plans = home.plans;
			p.dialog(
				t("home.plans.title"),
				[
					{
						fieldtype: "Select",
						fieldname: "plan_code",
						label: t("home.plan.code"),
						reqd: 1,
						default: code || plans[0].code,
						options: plans.map((v) => ({
							value: v.code,
							label: v.code + " · " + money(v.fee),
						})),
					},
				],
				t("home.plan.subscribe"),
				async ({ plan_code }, d) => {
					const result = await p.call(
						"pearnly_core.billing_www.subscribe",
						{ plan_code },
						"POST",
					);
					if (result.ok !== true) {
						frappe.msgprint({
							message: p.escape(result.reason || t("home.plan.failed")),
							indicator: "red",
						});
						return;
					}
					d.hide();
					p.success(t("home.plan.subscribed", plan_code, money(result.balance_after)));
					await refresh();
				},
			);
		}
		function topup() {
			let slip = "";
			const bank = home.collection_account;
			const d = p.dialog(
				t("home.topup"),
				[
					{ fieldtype: "HTML", options: `<p>${p.escape(t("home.topup.transfer"))}</p>` },
					read("bank", "home.bank.name", bank.bank + " · " + bank.branch),
					read("number", "home.bank.account", bank.account_number),
					read("holder", "home.bank.holder", bank.account_name),
					{ fieldtype: "Section Break" },
					{
						fieldtype: "Float",
						precision: 2,
						fieldname: "amount_thb",
						label: t("home.col.amount"),
						options: "THB",
						reqd: 1,
					},
					{ fieldtype: "Data", fieldname: "payer_name", label: t("home.field.payer") },
					{ fieldtype: "Small Text", fieldname: "note", label: t("home.col.note") },
					{
						fieldtype: "Button",
						fieldname: "upload",
						label: t("home.topup.slip"),
						click: () => {
							new frappe.ui.FileUploader({
								method: "pearnly_core.payments.proof.upload",
								allow_multiple: false,
								allow_web_link: false,
								allow_google_drive: false,
								allow_toggle_private: false,
								make_attachments_public: false,
								restrictions: {
									allowed_file_types: [".jpg", ".jpeg", ".png", ".pdf"],
									max_file_size: 10 * 1024 * 1024,
								},
								on_success(file) {
									slip = file.file_url;
									d.set_value("receipt", t("home.topup.attached"));
								},
							});
						},
					},
					{
						fieldtype: "Data",
						fieldname: "receipt",
						label: t("home.col.file"),
						read_only: 1,
					},
				],
				t("home.topup.submit"),
				async (values, dialog) => {
					const result = await p.call(
						"pearnly_core.billing_www.topup",
						{ ...values, slip_file: slip },
						"POST",
					);
					dialog.hide();
					p.success(
						t("home.topup.submitted", result.request, money(result.requested_thb)),
					);
					await refresh();
				},
			);
		}
		p.add_refresh(page, refresh);
		await refresh();
		timer = setInterval(() => {
			if (!document.hidden && !cur_dialog && $(wrapper).is(":visible"))
				void refresh().catch(() => {});
		}, 30000);
		$(wrapper).one("remove", () => clearInterval(timer));
	};
})();
