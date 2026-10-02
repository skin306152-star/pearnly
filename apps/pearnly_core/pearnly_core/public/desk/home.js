(() => {
	const p = frappe.pearnly;
	p.home = async (page, t, wrapper) => {
		let home, list;
		let tab = "usage", period = "all", pageLength = 20;
		let deleting = false, confirming = false, sequence = 0;
		const money = (value) => p.money(Number(value || 0));
		const read = (name, label, value, fieldtype = "Data") => ({
			fieldname: name,
			label: t(label),
			fieldtype,
			read_only: 1,
			default: value,
			options: fieldtype === "Currency" ? "THB" : undefined,
		});
		// 两层（用户 2026-09-30）：第一屏只放记录明细（与其它列表同一布局：页头下原生筛选条 + 整页官方列表），右上角
		// 「导出明细」「订阅与充值」；「订阅与充值」是二级页（面包屑 首页 / 订阅与充值），放账户余额与选择套餐，右上角
		// 「充值」「选择套餐」。同一个 Desk 页面按路由切换，照共用的路由写法：每次按当前路由重画、慢请求的旧结果丢掉
		// （原来用「正在刷新就跳过」的开关，快速点击 / 返回时界面慢一拍、点几次才进得去）。
		const title = page.title || __("Home");
		const on_billing = () => frappe.get_route()[1] === "billing";
		async function refresh() {
			const request = ++sequence;
			const loaded = await p.call("pearnly_core.billing_www.home");
			if (request !== sequence || frappe.get_route()[0] !== "pearnly-home") return;
			home = loaded;
			pageLength = list?.selected_page_count || pageLength;
			list?.destroy();
			list = null;
			p.clear_page(page);
			p.add_refresh(page, refresh);
			page.set_title_sub(
				home.pending_topups.length
					? p.escape(t("home.pending.count", home.pending_topups.length) + " " + t("home.pending.hint"))
					: "",
				"orange",
			);
			if (on_billing()) billing();
			else ledger();
		}
		function ledger() {
			page.set_title(title);
			p.breadcrumbs({ label: title, route: p.subroute("pearnly-home") });
			// 小的当场下载，大的排后台、做完推送后下载（`p.export_file`）。
			page.add_inner_button(t("home.ledger.export"), () =>
				void p.export_file("pearnly_core.billing_www.export_records"),
			);
			// 筛选放在页头下的原生筛选条（与客户、库存等列表一致），不再是表单里的两个下拉框。
			page.add_field({
				fieldtype: "Select", fieldname: "tab", label: __("Type"), default: tab,
				options: [
					{ value: "usage", label: t("home.ledger.usage") },
					...(home.can_manage ? [{ value: "topup", label: t("home.topup") }] : []),
					{ value: "ocr", label: t("home.ledger.ocr") },
				],
				change: () => { tab = page.fields_dict.tab.get_value() || "usage"; records(); },
			});
			page.add_field({
				fieldtype: "Select", fieldname: "period", label: __("Period"), default: period,
				options: ["all", "day", "month", "year"].map((value, i) => ({
					value,
					label: t(["home.period.all", "home.period.today", "home.period.month", "home.period.year"][i]),
				})),
				change: () => { period = page.fields_dict.period.get_value() || "all"; records(); },
			});
			records();
		}
		function billing() {
			const label = t("home.billing");
			page.set_title(label);
			p.breadcrumbs({ label: title, route: p.subroute("pearnly-home"), trail: [{ label }] });
			if (home.can_manage) {
				page.set_primary_action(t("home.topup"), topup);
				page.add_inner_button(t("home.plans.title"), subscription);
			}
			const sub = home.subscription;
			const form = p.layout(page.main, [
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
				{
					fieldtype: "HTML",
					options: home.pricing ? `<p class="text-muted">${p.escape(t("home.pricing.pages",
						home.pricing.pdf_first_pages, money(home.pricing.pdf_first_rate), money(home.pricing.pdf_after_rate)))}</p>
						<p class="text-muted">${p.escape(t("home.pricing.chars", home.pricing.chars_per_unit,
						money(home.pricing.char_rate)))}</p>` : "",
				},
				{ fieldtype: "Section Break", label: t("home.plans.title") },
				{ fieldtype: "HTML", fieldname: "plans" },
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
						label: sub?.plan_code === plan.code ? t("home.plan.current") : t("home.plan.subscribe"),
						click: () => subscription(plan.code),
					});
			}
			const plans = p.layout(form.fields_dict.plans.$wrapper, fields);
			plans.wrapper.find(".form-column").wrapInner('<div class="pearnly-plan"></div>');
		}
		function updateAction(rows) {
			if (on_billing()) return;  // 二级页的右上角是「充值」，不跟着明细勾选变
			if (home?.can_manage && rows.length) {
				page.set_primary_action(__("Delete"), removeSelected);
				page.btn_primary.prop("disabled", deleting || confirming);
			} else {
				page.set_primary_action(t("home.billing"), () => frappe.set_route("pearnly-home", "billing"));
				page.btn_primary.prop("disabled", false);
			}
		}
		function removeSelected() {
			if (deleting || confirming) return;
			const selectedTable = list, selectedTab = tab;
			const names = [...new Set(selectedTable.get_checked_items().map(row => row.name))];
			if (!names.length) return;
			confirming = true;
			updateAction(selectedTable.get_checked_items());
			const reset = () => {
				confirming = deleting = false;
				updateAction(list?.get_checked_items() || []);
			};
			const dialog = frappe.confirm(p.escape(t("home.ledger.confirmDelete", names.length)), async () => {
				if (deleting) return;
				deleting = true;
				try {
					const result = await p.call("pearnly_core.billing_www.delete_records",
						{tab: selectedTab, names}, "POST");
					if (result.deleted) p.success(t("home.ledger.deletedCount", result.deleted), "delete");
					if (list === selectedTable) await list.reload();
				} catch (error) {
					if (!error?._server_messages && !error?.responseJSON) console.error(error);
				} finally { reset(); }
			}, reset);
			dialog.onhide = () => { if (!deleting) reset(); };
		}
		function records() {
			pageLength = list?.selected_page_count || pageLength;
			list?.destroy();
			updateAction([]);
			const activeTab = tab, activePeriod = period;
			const status = code => t({pending: "home.status.pending", approved: "home.status.credited",
				rejected: "home.status.rejected", ok: "home.status.ok", needs_review: "home.status.toreview",
				failed: "home.status.failed"}[code] || code);
			const columns = activeTab === "topup"
				? [["creation", "time"], ["name", "reference"], ["status", "status", status],
					["requested_thb", "requested", money], ["approved_thb", "received", money],
					["note", "note", (v, row) => row.review_note || v || "—"]]
				: activeTab === "ocr"
					? [["creation", "time"], ["status", "result", status], ["filename", "file"],
						["charged_thb", "charges", v => v == null ? "—" : money(v)],
						["detail", "summary", (v, row) => row.error || v || "—"]]
					: [["creation", "time"], ["product", "product"], ["pages", "pages"],
						["amount_thb", "amount", money], ["balance_after", "balanceAfter", money],
						["description", "description", value => __(value || "")]];
			// 整页官方列表（与其它列表同一布局），不再嵌在表单里。
			list = p.list({page, page_length: pageLength, compact_columns: true,
				doctype: "Pearnly OCR Record", selectable: home.can_manage,
				on_selection: updateAction,
				columns: columns.map(([fieldname, label, format]) => ({fieldname,
					// 与产品里其它时间一致：`p.datetime_text` 去掉微秒与秒（`str_to_user` 会带上 `:ss`）。
					label: t("home.col." + label), format: fieldname === "creation"
						? value => p.datetime_text(value) : format})),
				load: async ({start, page_length}) => {
					const result = await p.call("pearnly_core.billing_www.records", {
						tab: activeTab, period: activePeriod, offset: start, limit: page_length});
					return {items: result.rows, total: result.total};
				},
			});
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
		wrapper.on_page_show = () => { p.restore_sidebar(); void refresh(); };
		$(wrapper).on("hide", () => { ++sequence; });
		await refresh();
		// 刷新（`p.live`）：本人的识别 / 对账一结束（会扣费、出明细）服务端就推实时事件，收到就刷；平时 60 秒兜底一次（原来
		// 固定 30 秒），页面看不见不刷，回到页面马上刷一次。明细页只刷列表（勾选、弹窗、正在改筛选时不刷），订阅与充值页重画余额。
		const live = p.live({
			refresh: async () => {
				const editing = $(document.activeElement).is("input,select,textarea,[role=combobox]");
				if (cur_dialog || editing || deleting || confirming || !$(wrapper).is(":visible")) return;
				if (on_billing()) await refresh();
				else if (!list?.get_checked_items().length) await list?.refresh();
			},
			events: ["pearnly_ocr_work", "cowork_recon_finished"],
			// 只认有结论的（识别结束、对账结束）；每读完一页的进度推送（只带 pages_done）不刷
			accept: data => data?.job !== undefined
				|| (Boolean(data?.status) && !["queued", "running", "deferred"].includes(data.status)),
		});
		$(wrapper).one("remove", () => live.stop());
	};
})();
