/* Scan -> observe actual fields -> save -> next. One native Dialog, one camera stream.
 *
 * Cowork 盘点记录弹窗：ERP `pearnly_erp_portal/public/desk/stocktake-entry.js` 的原样副本（用户 2026-09-26
 * “其它照搬”）。只改了接口前缀、词条前缀，以及“实际仓库”由 ERP 仓库下拉改为填写（Cowork 没有仓库主档）。
 * 读码器与 ERP 共用 `pearnly_core/public/desk/scan.js`（ERP 2026-09-26 去重后也只用这份）。
 * 改 ERP 的盘点记录弹窗时请同步这份弹窗。
 */
(() => {
	const p = frappe.pearnly, api = "pearnly_cowork.stocktake.desk_api.";
	// 一条记录最多 5 张照片（服务端 `counts.PHOTO_LIMIT` 同值，它才是权威，这里只是先拦一下）。
	const PHOTO_MAX = 5;
	// `scan`：一打开就开扫码器（从 LINE 普通内置浏览器转到 LIFF 回来时，接着扫）
	p.stockCountEntry = async ({ count, t, row, entry, continuous = false, scan = false, on_saved, on_done }) => {
		let selected, lookup, request_id, scanner, busy = false, closed = false, camera = false;
		// `scanned`：这一件是扫码来的，点「继续」后自动再开扫码器，打字选的回到框里（用户 11-02）
		let scanned = false, last_code = "";
		let last_place = null;
		// 扫码器收起后多久真关相机（手机状态栏的相机指示一直亮着）
		const PARK_MS = 3 * 60 * 1000;
		let park_timer;
		// `photos` 是本次要随记录提交的（桌面端上传后拿到 file_url，服务端再挂到这条记录上）；
		// `existing` 是这条记录**已经有**的照片，只做只读预览（旧项目也没有删照片的入口）。
		const photos = [];
		let existing = [];
		const w = key => t(`cowork.stock.workbench.${key}`);
		const fields = [
			{ fieldtype: "Section Break", fieldname: "scan_section" },
			// 一个框（用户 11-02）：打字出本次任务上传的物料（原生选择框，服务端按页查），框内右侧扫码图标开扫码器；
			// 扫码枪 / 手打编码回车按码认。扫码器平时收起，读到物料就关。
			{ fieldtype: "Link", fieldname: "code", label: w("code"), description: w("scanHint"), options: "Cowork Stocktake Item",
				// 下拉里不给「新建物料」：新建只在扫到 / 输入系统没有的码时给（用户 11-03）
				only_select: true, ignore_link_validation: 1, pearnly_no_create: 1,
				get_query: () => ({ query: api + "api_stock_count_item_search", filters: { count } }),
				// 扫码器开着时再点一下图标就是关掉
				scan: () => void (camera ? close_camera() : start_camera()) },
			// 「关闭」在画面上面：手机上画面很高，放下面会落到底栏后面看不到
			{ fieldtype: "Button", fieldname: "close_camera", label: __("Close"), hidden: 1, click: () => close_camera() },
			{ fieldtype: "HTML", fieldname: "video", options: p.scan_stage(), hidden: 1 },
			{ fieldtype: "Check", fieldname: "torch", label: __("cowork.stock.scan.light"), hidden: 1,
				change: () => scanner?.torch(Boolean(d.get_value("torch"))) },
			{ fieldtype: "HTML", fieldname: "message" },
			{ fieldtype: "Section Break", fieldname: "entry_section", hidden: 1 },
			{ fieldtype: "Data", fieldname: "product_name", label: t("cowork.stock.col.name"), read_only: 1 },
			{ fieldtype: "Select", fieldname: "source_row", label: w("bookPosition"), options: [],
				change: () => choose_source() },
			{ fieldtype: "Data", fieldname: "unit", label: t("cowork.stock.col.unit"), read_only: 1 },
			{ fieldtype: "Data", fieldname: "warehouse", label: w("actualWarehouse"), reqd: 1 },
			// 「这件货还在别处」：货的账可能在另一个仓/仓位（用户 2026-09-23 那次盘点就是这样，
			// 数出来的货落成了新位置）。摆在"实际仓库"下面，当场就能改（见 `render_elsewhere`）。
			{ fieldtype: "HTML", fieldname: "elsewhere", hidden: 1 },
			// 多人分区盘点：这件货在本次盘点里别人（或自己）已经记过的 —— 谁、在哪、多少，
			// 免得同一架货被两个人各数一遍（用户 2026-10-15）。没有就不占地方。
			{ fieldtype: "HTML", fieldname: "recorded", hidden: 1 },
			{ fieldtype: "Data", fieldname: "location", label: w("actualLocation") },
			{ fieldtype: "Float", fieldname: "qty", label: w("entryQty"), precision: 3, reqd: 1 },
			{ fieldtype: "Small Text", fieldname: "note", label: t("cowork.stock.count.note") },
			{ fieldtype: "Section Break", fieldname: "photo_section", hidden: 1 },
			{ fieldtype: "HTML", fieldname: "photos", hidden: 1 },
		];
		const d = p.dialog(entry ? w("editEntry") : (continuous ? w("continuous") : w("record")), fields,
			continuous ? w("saveNext") : __("Save"), async values => {
				if (!selected || busy) return;
				// Native Float normalizes an empty input to 0; an observation must be explicit.
				if (!String(d.fields_dict.qty.$input.val()).trim()) {
					frappe.msgprint(__("Enter a quantity"));
					d.fields_dict.qty.$input.trigger("focus");
					return;
				}
				busy = true;
				try {
					const payload = { qty: values.qty, warehouse: values.warehouse,
						location: values.location || "", note: values.note || "", stock_token: lookup.stock_token,
						photos: photos.map(photo => photo.url) };
					if (entry) await p.call(api + "api_stock_count_edit_entry",
						{ ...payload, entry: entry.name, version: entry.version }, "POST");
					else await p.call(api + "api_stock_count_add_entry", { ...payload, count,
						item: values.source_row || undefined, product: selected.name, request_id }, "POST");
					last_place = { warehouse: values.warehouse, location: values.location || "" };
					p.success(t("cowork.stock.count.entrySaved"), "click");
					photos.length = 0;
					await on_saved?.();
					if (closed) return;
					if (!continuous) d.hide();
					else await next();
				} finally { busy = false; }
			});
		d.$wrapper.find(".modal-dialog").addClass("modal-lg");
		// 触屏上打开不自动聚焦框（框架默认聚焦第一格：键盘弹起、下拉展开，挡住扫码），要打字时点框；电脑照框架默认
		if (!window.matchMedia?.("(pointer: fine)").matches) d.no_focus = true;
		d.set_secondary_action(() => { d.hide(); on_done?.(); });
		// 连续盘点时第二颗是"完成"：收工离开扫码，落到差异那一屏（用户 2026-09-22 口径：
		// "…下一项或者完成"）。单条录入/修改时它仍是"查看差异"。
		d.set_secondary_action_label(continuous ? w("done") : w("review"));
		d.onhide = () => { closed = true; stop_camera(); };  // 关掉弹窗才真关相机（收起着的那一路也放掉）
		const code = d.fields_dict.code;
		// 框里带着盘点号查，原生下拉会多一行「已应用筛选 …」把盘点号露出来：这一格不要那一行
		code.get_filter_description = async () => null;
		// 下拉第二行只留「编码 · 条码」：框架对显示名称的选择框会把内部编号拼在最前面（Cowork 的行号是一串随机号）
		const merge = code.merge_duplicates.bind(code);
		code.merge_duplicates = rows => merge(rows).map(row => ({ ...row,
			description: String(row.description || "").split(", ").filter(part => part !== row.value).join(", ") }));
		// 回车：扫码枪 / 手打的码按码认（捕获阶段先于原生下拉，免得它把第一项当成选中）；用方向键挑了一项才交给原生选中
		let navigated = false;
		code.input.addEventListener("keydown", event => {
			if (event.key === "ArrowDown" || event.key === "ArrowUp") { navigated = true; return; }
			if (event.key !== "Enter" || event.isComposing) return;
			if (navigated && code.awesomplete?.isOpened) return;
			event.preventDefault(); event.stopImmediatePropagation();
			code.awesomplete?.close();
			scanned = false;
			void match(String(code.$input.val() || "").trim());
		}, true);
		code.input.addEventListener("input", () => { navigated = false; });
		// 从下拉里选了一件：直接开这件的录入
		code.input.addEventListener("awesomplete-selectcomplete", event => {
			const value = event.text?.value;
			if (!value || String(value).endsWith("__link_option")) return;
			scanned = false;
			void match(null, value);
		});
		// A scanner's Enter finds the product; it must not submit the observation.
		code.$input.off("keypress").on("keypress", event => {
			if (event.key === "Enter") { event.preventDefault(); event.stopPropagation(); }
		});
		function message(text) { d.fields_dict.message.$wrapper.empty().append($("<p class='text-muted'>").text(text)); }
		// 「这件货还在别处」：`lookup.elsewhere` 是服务端给的"别的仓/仓位还有多少"。
		// 有就摆出来，没有就不占地方（用户 2026-09-23："盘点时不提示'这件货的账在别处'"）。
		function render_elsewhere() {
			const host = d.fields_dict.elsewhere;
			if (!host) return;
			const places = lookup?.elsewhere || [];
			host.$wrapper.empty();
			d.set_df_property("elsewhere", "hidden", places.length ? 0 : 1);
			if (!places.length) return;
			const text = places.slice(0, 3)
				.map(place => `${place.warehouse_name} / ${place.location || t("cowork.stock.count.blank")} · `
					+ `${p.number(place.qty)} ${place.unit || ""}`.trim())
				.join("；");
			host.$wrapper.append($("<p class='text-muted small mb-0'>").text(`${w("elsewhere")} ${text}`));
		}
		function render_recorded() {
			const host = d.fields_dict.recorded;
			const records = entry ? [] : lookup?.recorded || [];
			host.$wrapper.empty();
			d.set_df_property("recorded", "hidden", records.length ? 0 : 1);
			if (!records.length) return;
			const text = records.slice(0, 5)
				.map(record => `${record.counted_by} · ${record.warehouse_name} / ${record.location || t("cowork.stock.count.blank")} · `
					+ p.number(record.qty))
				.join("；");
			host.$wrapper.append($("<p class='text-muted small'>").text(`${w("recorded")} ${text}`));
		}
		function editor(show) {
			d.set_df_property("scan_section", "hidden", show);
			d.set_df_property("entry_section", "hidden", !show);
			d.set_df_property("photo_section", "hidden", !show);
			d.set_df_property("photos", "hidden", !show);
			d.get_primary_btn().toggle(show);
		}
		// ── 照片 ────────────────────────────────────────────────────────────────
		// 原生上传器（`frappe.ui.FileUploader`）：多选、可选拍照，默认存成私有文件；
		// 这里只收集 `file_url`，提交时由服务端把 File 挂到这条记录上（`counts._save_photos`）。
		function pick_photos() {
			stop_camera();  // 拍照要用手机自己的相机：收着的那一路先放掉
			const left = PHOTO_MAX - existing.length - photos.length;
			if (left <= 0) { frappe.msgprint(w("photoLimit")); return; }
			new frappe.ui.FileUploader({
				allow_multiple: true,
				allow_take_photo: true,
				allow_toggle_private: false,
				restrictions: { allowed_file_types: ["image/*"] },
				on_success: file => {
					if (!file?.file_url) return;
					if (existing.length + photos.length >= PHOTO_MAX) { frappe.msgprint(w("photoLimit")); return; }
					photos.push({ url: file.file_url, name: file.file_name || String(file.file_url).split("/").pop() });
					render_photos();
				},
			});
		}
		function render_photos() {
			const host = d.fields_dict.photos?.$wrapper;
			if (!host) return;
			const total = existing.length + photos.length;
			host.empty();
			$("<p class='text-muted'>").text(`${w("photos")} ${total} / ${PHOTO_MAX} · ${w("photoHint")}`).appendTo(host);
			const strip = $("<div class='pearnly-count-photos'>").appendTo(host);
			const thumb = (url, name, removable) => {
				const cell = $("<div class='pearnly-count-photo'>").appendTo(strip);
				$("<img>").attr({ src: url, alt: name }).appendTo(cell);
				if (!removable) return;
				$("<button type='button' class='btn btn-default btn-xs'>").text(w("removePhoto"))
					.on("click", () => {
						const index = photos.findIndex(photo => photo.url === url);
						if (index >= 0) photos.splice(index, 1);
						render_photos();
					}).appendTo(cell);
			};
			existing.forEach(photo => thumb(photo.file_url, photo.file_name || "", false));
			photos.forEach(photo => thumb(photo.url, photo.name, true));
			if (total < PHOTO_MAX) {
				$("<button type='button' class='btn btn-default btn-sm'>").text(w("addPhoto"))
					.on("click", () => pick_photos()).appendTo(host);
			}
		}
		async function load_photos() {
			if (!entry?.name) return;
			existing = await p.call(api + "api_stock_count_photos", { entry: entry.name });
			render_photos();
		}
		async function choose_source() {
			if (!lookup || !selected) return;
			const source = lookup.rows.find(r => r.name === d.get_value("source_row"));
			await d.set_value("warehouse", entry?.warehouse_name || source?.warehouse_name || lookup.warehouse_name);
			await d.set_value("location", entry?.location ?? source?.location ?? "");
		}
		async function match(code, product) {
			if (busy || selected || closed || (!code && !product)) return;
			busy = true; scanner?.pause();
			try {
				lookup = await p.call(api + "api_stock_count_lookup", { count, code: code || undefined, product });
				if (closed) return;
				if (lookup.not_product_code) {
					// 扫到的是网址（TIS 认证码等）：不是商品码，说一句，扫码器接着开、先跳过这个码（同 ERP，判断在共用层 `scan_codes`）
					message(lookup.not_product_code);
					frappe.show_alert({ message: lookup.not_product_code, indicator: "orange" });
					lookup = null;
					rescan(code);
					if (code) p.scan_result_sound(false);
					return;
				}
				selected = lookup.product;
				request_id = crypto.randomUUID();
				d.set_df_property("source_row", "options", lookup.rows.map(r => ({
					value: r.name, label: `${r.warehouse_name} / ${r.location || t("cowork.stock.count.blank")} · ${p.number(r.book_qty)} ${r.unit || ""}`,
				})));
				d.set_df_property("source_row", "hidden", !lookup.rows.length);
				await d.set_value("source_row", row?.name || lookup.rows[0]?.name || "");
				await d.set_value("product_name", selected.product_name);
				await d.set_value("unit", selected.base_unit || selected.unit || "");
				await choose_source();
				// 沿用上一件的位置只给**账上没有位置**的新品（同一个货架连续盘时省得重填）；这件在账上有位置
				// 就用账面位置，否则会盖掉它、凭空多出一条位置差异，结束盘点时真的调库存（录制问题 #21）。
				// 修改已录的记录也不沿用：那条记录自己的位置才是对的。
				if (last_place && !entry && !lookup.rows.length) {
					await d.set_value("warehouse", last_place.warehouse);
					await d.set_value("location", last_place.location);
				}
				await d.set_value("qty", entry ? entry.qty : "");
				if (!entry) d.fields_dict.qty.$input.val("");
				await d.set_value("note", entry?.note || "");
				park_camera();  // 认出来、字段都填好了：扫码器收起，直接出这件的字段（中途出错就不收，照常跳过这个码接着扫）
				editor(true);
				await load_photos();
				render_photos();
				render_elsewhere();
				render_recorded();
				d.fields_dict.qty.$input.trigger("focus");
				// 结果那一声：查到了才响（从码来的那一次才算 —— 打开已录那一行不算"查了"）。
				if (code) p.scan_result_sound(true);
			} catch (error) {
				selected = null;
				// 服务端明确不认（417：查不到这个码）才说"找不到"；网络断、服务端出错、这页自己的脚本错都只说查找失败，
				// 错误照记，不冒充"找不到"。读码器跳过这个码：镜头还对着它就不再反复查（换个码或手动点查找再试）。
				const refused = error?.status === 417;
				// 417 时服务端那句已经弹成原生提示框，这里只写进弹窗；查找失败没人提示过，再浮动提示一次
				// （相机开着时弹窗里那一行在画面下面，手机上看不到）。
				if (refused) message(w("lookupFailed"));
				else { message(w("lookupError")); frappe.show_alert({ message: w("lookupError"), indicator: "orange" }); }
				// 这页自己的脚本错交给统一的前端报错（服务端的错服务端自己记）。
				if (!refused) console.error(error);
				if (!error?.status) window.pearnlyReportError?.(error, "stocktake-lookup");
				rescan(code);
				if (code) p.scan_result_sound(false);
			} finally { busy = false; }
		}
		// `skip`：先跳过这个码，直到镜头里出现别的码（「继续」重开时镜头往往还对着刚盘的那件，免得马上又记一次）。
		// 收起着（`park_camera`）就在同一路相机上恢复，不重新要相机。
		async function start_camera(skip = "") {
			if (closed) return;
			if (scanner) { if (!camera) unpark_camera(skip); return; }
			// 安卓 LINE 普通内置浏览器里网页拿不到相机：直接转 LIFF 打开这份盘点并接着扫（LINE 弹原生授权），
			// 这一页的扫码窗口随即关掉（LIFF 盖在它上面打开，关掉 LIFF 回来不留一个黑着的相机框）。
			if (p.scan_liff_first(true)) { d.hide(); return; }
			camera = true;
			d.set_df_property("video", "hidden", false);
			d.set_df_property("close_camera", "hidden", false);
			scanner = p.scan_reader({ host: d.fields_dict.video.$wrapper, kind: "all",
				on_result: value => { scanned = true; last_code = value; return match(value); },
				on_error: error => {
					scanner?.stop(); scanner = null; camera = false;
					// 在 LINE 普通内置浏览器里：转 LIFF 打开这份盘点并接着扫（LINE 会弹原生相机授权）。
					if (p.scan_failure(error) === "in_line" && p.scan_to_liff(true)) { d.hide(); return; }
					// 打不开就把黑框收起来，只留原因那一句（手动输入编码照常可用）。
					d.set_df_property("video", "hidden", true); d.set_df_property("torch", "hidden", true);
					d.set_df_property("close_camera", "hidden", true);
					p.scan_hint(d.fields_dict.message.$wrapper, error, restart_camera);
				},
				// App 内置浏览器里 5 秒还没开相机（请求被挂着）：LINE 里转 LIFF，别的给原因、继续等。
				on_waiting: reason => {
					if (reason === "in_line" && p.scan_to_liff(true)) { scanner?.stop(); d.hide(); return; }
					p.scan_hint(d.fields_dict.message.$wrapper, reason, restart_camera);
				},
				on_started: () => d.fields_dict.message.$wrapper.empty(),
			});
			await scanner.start();
			if (skip) scanner?.resume(skip);
			d.set_df_property("torch", "hidden", !scanner?.has_torch());
		}
		// 扫码器开着时只在这里换画面显隐：`set_df_property("video", …)` 会重画这一格、换掉 <video>，相机流就接在被换掉的那个上（10-23 黑屏）
		function show_stage(show) { d.fields_dict.video.$wrapper.toggleClass("hide-control", !show); }
		// 读到物料 / 手动关：只收起画面、停读码，相机流留着，「继续」或再点图标时接着用（LINE 的相机授权只有「仅允许本次」，
		// 重新要相机每次都会再问，用户 11-03）。关掉弹窗、去拍照、收起超过 PARK_MS 才真关。
		function park_camera() {
			if (!scanner) return stop_camera();
			scanner.pause(); camera = false;
			if (d.get_value("torch")) { void scanner.torch(false).catch(window.pearnlyReported(null, "scan")); void d.set_value("torch", 0); }
			show_stage(false);
			d.set_df_property("torch", "hidden", true); d.set_df_property("close_camera", "hidden", true);
			window.clearTimeout(park_timer);
			park_timer = window.setTimeout(stop_camera, PARK_MS);
		}
		function unpark_camera(skip) {
			const video = d.fields_dict.video.$wrapper.find("video").get(0);
			// 收起期间相机被别的应用占走、流断了：照新开（会再要一次相机）
			if (!video?.srcObject?.getVideoTracks?.().some(track => track.readyState === "live")) {
				stop_camera(); void start_camera(skip); return;
			}
			window.clearTimeout(park_timer);
			camera = true;
			show_stage(true);
			d.set_df_property("close_camera", "hidden", false);
			d.set_df_property("torch", "hidden", !scanner.has_torch());
			// iPhone 上收起的 <video> 会被暂停：再播；被拦了就等人点一下画面（同读码器的 `on_play_blocked`）
			const play = () => video.play();
			play().catch(() => $(video).closest(".pearnly-scan-stage").one("click", () => void play().catch(window.pearnlyReported(null, "scan"))));
			scanner.resume(skip);
		}
		// 认码没成、要接着扫：只有扫码器开着才接着读（收起着时打字查的码不该让藏着的相机开始读）
		function rescan(value) { if (camera) scanner?.resume(value); }
		function stop_camera() {
			window.clearTimeout(park_timer);
			if (!camera && !scanner) return;
			scanner?.stop(); scanner = null; camera = false;
			d.set_df_property("video", "hidden", true); d.set_df_property("torch", "hidden", true);
			d.set_df_property("close_camera", "hidden", true);
		}
		// 手动关掉扫码器：回到框里
		function close_camera() {
			park_camera();
			code.$input.trigger("focus");
		}
		// 去设置里开了权限再切回来：重开相机（`p.scan_hint` 只在需要去设置时调它）。
		function restart_camera() {
			if (closed) return;
			scanner?.stop(); scanner = null;
			void start_camera();
		}
		async function next() {
			selected = null; lookup = null; row = null; entry = null;
			existing = [];
			photos.length = 0;
			await d.set_value("code", ""); await d.set_value("qty", "");
			editor(false); render_photos(); message(w("nextReady"));
			render_elsewhere();   // 换下一条：别处那份提示也收起来
			render_recorded();
			// 上一件是扫码来的：接着开扫码器；打字选的：回到框里
			if (scanned) void start_camera(last_code);
			else code.$input.trigger("focus");
		}
		editor(false);
		if (row) await match(null, row.product);
		else if (scan) await start_camera();
	};
})();
