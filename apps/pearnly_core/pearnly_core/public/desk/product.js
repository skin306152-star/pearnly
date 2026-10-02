/* Native Desk helpers: no cloned widgets, no Vue/Tailwind inside Desk. */
frappe.provide("frappe.pearnly");
(() => {
	const p = frappe.pearnly;
	p.call = async (method, args = {}, type = "GET") =>
		(await frappe.call({ method, args, type })).message;
	p.escape = (value) => frappe.utils.escape_html(String(value ?? ""));
	// 数量与金额**按实际值**显示：不留固定的尾随 0（8.000 → 8，3.500 → 3.5，฿190.00 → ฿190）。
	// 用户 2026-09-22 定："那个数量后面留两个 0 就好了或者不要固定，自动填……按实际显示"。
	// 上限仍然守着（数量 3 位、金额 2 位），免得浮点尾巴漏到界面上。
	// 分组与小数分隔符交给框架自己的 `format_number` / `format_currency`，这里只算"要几位"。
	const decimals_of = (value, max) => {
		const number = Number(value);
		if (!isFinite(number)) return null;
		const fraction = Math.abs(number).toFixed(max).split(".")[1] || "";
		return fraction.replace(/0+$/, "").length;
	};
	p.number = (value, max = 3) => {
		if (value === null || value === undefined || value === "") return "";
		const decimals = decimals_of(value, max);
		return decimals === null ? String(value) : format_number(value, null, decimals);
	};
	// 数量/百分数按**实际位数**显示：2.5 就是 "2.5"、2 就是 "2"，不留 `precision` 那些尾零。
	//
	// 为什么这么做：框架的 Float 控件用 `format_for_input` = `format_number(value, null, precision)`
	// 渲染输入框，precision 是字段定的（明细数量给 6），所以 2.5 会显示成 2.500000。
	// 这里只改**显示**这一层（包一层 format_for_input 去尾零），值不动 —— 输入解析
	// （`parse` → `flt(value, precision)`）走的是另一条路，所以"2.5"照样能输入、不会被四舍五入。
	// 金额（Currency）不在这里改：钱固定两位。
	p.tidy_number = (field) => {
		const type = field?.df?.fieldtype;
		if (!field || !["Float", "Percent"].includes(type) || field.pearnly_tidy) return field;
		field.pearnly_tidy = true;
		const tidy = text => (typeof text === "string" && text.includes(".")
			? text.replace(/(\.\d*?)0+$/, "$1").replace(/\.$/, "") : text);
		const format = field.format_for_input?.bind(field);
		if (format) field.format_for_input = value => tidy(format(value));
		// 只读时框架走的不是 `format_for_input`，而是 `frappe.format(value, df)`；它的
		// Float 分支把 precision 0 当"没设"退回系统默认（2 → 2.000、2.5 → 2.500）。
		// 数量按**实际位数**显示，所以只读的 Float 直接出我们自己那份文本。
		const display = field.set_disp_area?.bind(field);
		if (display) field.set_disp_area = value => {
			const shown = value ?? field.get_value?.();
			if (field.df.read_only && type === "Float" && field.disp_area
					&& shown !== null && shown !== undefined && shown !== "") {
				$(field.disp_area).html(p.escape(p.number(shown)));
				return;
			}
			return display(value);
		};
		// 已经渲染过的控件（默认值先画了一遍）按新口径重画一次显示。
		if (field.$input?.length) field.set_formatted_input(field.get_value?.());
		return field;
	};
	p.money = (value, currency = "THB", max = 2) => {
		if (value === null || value === undefined || value === "") return "";
		const decimals = decimals_of(value, max);
		return decimals === null ? String(value) : format_currency(value, currency, decimals);
	};

	// ── 货币符号：全站一律出符号（฿），不出货币代码（THB）────────────────────────────
	// 用户 2026-09-23 口径："全站货币改为泰铢的符号，不要字母"。
	//
	// 框架那条路（`format_currency` → `get_currency_symbol`）取符号的方式是
	// `frappe.model.get_value(":Currency", <货币代码>, "symbol")`，没有就**退回货币代码本身** ——
	// 所以页面上到处是 "THB 62.00"。这里两处一起修：
	//   ① **把符号喂进框架的缓存**（`locals[":Currency"]`，就是从站点数据库读一次）；
	//   ② 给 `format_currency` 加一层兜底：万一还没喂到（首屏那一瞬），也把代码换成符号。
	// 两处都在公共层 → 商品、库存、采购、销售、收银（含搬过来的官方收银台）与所有框架表格一起生效。
	const SYMBOLS = {
		THB: "฿", USD: "$", EUR: "€", JPY: "¥", CNY: "¥", GBP: "£",
		LAK: "₭", KHR: "៛", MMK: "K", VND: "₫", SGD: "S$", MYR: "RM", IDR: "Rp",
	};
	const code_of = currency => currency || (frappe.boot.sysdefaults || {}).currency || "THB";
	// 框架取符号那条路，**不传币种时**读的是 `frappe.boot.sysdefaults.currency`，而它来自
	// `DefaultValue` 表。我们自己供的站点在初始化时没写这一行（见补丁
	// `pearnly_core/patches/v0_0_4_site_default_currency.py`），断在链子后面的样子是：
	// `get_currency_symbol(undefined)` → `get_value(":Currency", undefined, "symbol")`
	// 命中缓存里**第一条**货币 → 页面上的金额全变成 `د.إ 100.00`（本机实测）。
	// 服务端那个补丁是根治；这里再钉一次，保证迁移之前打开的老页面也不会花。
	const site_currency = () => {
		const sysdefaults = (frappe.boot.sysdefaults = frappe.boot.sysdefaults || {});
		if (!sysdefaults.currency) sysdefaults.currency = "THB";
		return sysdefaults.currency;
	};
	// ① 从站点读一次符号，放进框架自己的缓存（之后 `frappe.format`、`format_currency` 都走它）。
	// 符号从**服务端启动数据**来（`pearnly_core/desk/currency.py` 读到站点配的那一个），
	// 本文件这张表只做兜底。**别改成前端去读 `Currency` 表** —— 那张表的权限是收紧的，
	// 读一次就弹一个「无权限操作货币」的 403 弹窗（本机实测，收银台进页面就弹）。
	const symbol_of = (code) => {
		const from_boot = frappe.boot.pearnly_currency;
		if (from_boot && from_boot.code === code && from_boot.symbol) return from_boot.symbol;
		return SYMBOLS[code];
	};
	const warm_symbol = (code) => {
		const symbol = symbol_of(code);
		if (!code || !symbol) return;
		locals[":Currency"] = locals[":Currency"] || {};
		if (locals[":Currency"][code]) return;
		locals[":Currency"][code] = { doctype: ":Currency", name: code, symbol };
	};
	warm_symbol(site_currency());
	// ② 兜底：框架把符号取成货币代码时（"THB 62.00"），换成符号。
	const native_format_currency = window.format_currency;
	if (native_format_currency && !window.__pearnly_symbol_wrapper) {
		window.__pearnly_symbol_wrapper = true;
		window.format_currency = function (value, currency, decimals) {
			const out = native_format_currency.apply(this, arguments);
			const code = code_of(currency);
			const symbol = SYMBOLS[code];
			if (typeof out === "string" && symbol && out.indexOf(code + " ") === 0) {
				return symbol + out.slice(code.length);
			}
			return out;
		};
	}
	// 产品里的时间一律这么显示：`2026-09-22 20:10`（到分钟）。所有界面（列表、弹窗、明细）
	// 都走这一个入口 —— 用户 2026-09-22 口径："我真的不希望说一处就该一处"。
	//
	// 为什么这里**不做** `str_to_user` 的时区换算：业务时间在库里就是**站点时区的裸时间**
	// （盘点记录 `counted_at`、收银单据 `sold_at` 都是站点时区的 now() 写进去的），再按
	// "UTC → 观看者时区"换算只会整体偏 —— 本机实测偏 1.5 小时（站点默认时区残留成
	// Frappe 装的 Asia/Kolkata）。旧项目同样按业务时区（UTC+7）直接显示。
	// 所以只做三件事：去掉 `T`、去掉微秒尾巴、秒收到分钟。
	p.datetime_text = (value) => {
		if (!value) return "";
		return String(value).replace("T", " ").replace(/\.\d+$/, "")
			.replace(/(\d{1,2}:\d{2}):\d{2}$/, "$1");
	};
	// 纯数字金额（不带货币符号、固定两位小数）：收发存那种"整列同一个币种"的表用 ——
	// 旧项目那张表的金额列就是 `20,000.00` 这个形状（`fmtMoney`），不带 THB。
	p.amount = (value) => {
		if (value === null || value === undefined || value === "") return "";
		const number = Number(value);
		return isFinite(number) ? format_number(number, null, 2) : String(value);
	};
	// 数量**不加千分位**（`2000`，不是 `2,000`）：旧项目那张收发存表的数量列就是
	// `fmtQty` 的 `String(n)`。站点的数字格式里分组符号可能是逗号也可能是点，所以从
	// 框架的格式信息里取出来再去掉，不写死分隔符。
	p.plain_number = (value, max = 3) => {
		const text = p.number(value, max);
		if (!text) return text;
		const info = typeof get_number_format_info === "function" && typeof get_number_format === "function"
			? get_number_format_info(get_number_format())
			: { group_sep: ",", decimal_str: "." };
		const parts = String(text).split(info.decimal_str);
		const integer = parts[0].split(info.group_sep).join("");
		return parts.length > 1 ? `${integer}${info.decimal_str}${parts[1]}` : integer;
	};
	// 佛历日期 `08/04/2569`：旧项目 `format-date.ts` 的 `DD/MM/YYYY`（年份 +543），
	// 收发存那张报表按它显示（用户 2026-09-22 给的参考图就是这个形状）。
	p.buddhist_date = (value) => {
		const parts = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(value || ""));
		return parts ? `${parts[3]}/${parts[2]}/${Number(parts[1]) + 543}` : String(value || "");
	};
	// ── 列表刷新：推送为主，定时兜底（2026-10-01 用户经压测窗口同意）────────────────────────────────
	// 原来各页固定每几秒问一次（对账列表 5 秒、录入有录入中的 4 秒、首页 30 秒），几百人停在页面上光这些就每秒上百个请求。
	// 现在：收到服务端的实时事件（`events`，`accept(data)` 过滤）就刷新，同一阵子来的多个事件合成一次；兜底的定时：本页有
	// 自己在跑的任务（`busy()`）时 `busy_ms`（默认 5 秒），否则 `idle_ms`（默认 60 秒）；页面看不见时不刷，重新看得见 /
	// 窗口拿回焦点时马上刷一次（与 react-query 的 refetchOnWindowFocus 同一做法）。别人做的改动没有推给本人的，最晚 60 秒出现。
	// `still_current()` 为假（切走了）或调了返回的 `stop()` 就停，事件监听一并摘掉。`refresh` 抛错不影响下一次。
	// 限频：两次刷新至少隔 `min_ms`（默认 3 秒）——事件只"去抖"不够，小助手逐张推 50 张、事件间隔都过了去抖时间，就会
	// 整表重拉 50 次（独立复查 2026-10-01）。实时连接断了再连上（部署时 socketio 重启）马上刷一次，补上断线期间漏掉的事件。
	p.live = ({ refresh, events = [], accept = () => true, busy = () => false, still_current = () => true,
		busy_ms = 5000, idle_ms = 60000, settle_ms = 800, min_ms = 3000 }) => {
		let timer = 0, soon = 0, running = false, again = false, stopped = false, last = 0;
		const handlers = [];
		const socket = frappe.realtime?.socket;
		const stop = () => {
			if (stopped) return;
			stopped = true;
			clearTimeout(timer);
			clearTimeout(soon);
			for (const [event, handler] of handlers) frappe.realtime?.off?.(event, handler);
			socket?.off?.("connect", on_visible);
			document.removeEventListener("visibilitychange", on_visible);
			window.removeEventListener("focus", on_visible);
		};
		const schedule = () => {
			clearTimeout(timer);
			if (!stopped && !document.hidden) timer = setTimeout(run, busy() ? busy_ms : idle_ms);
		};
		async function run() {
			clearTimeout(timer);
			clearTimeout(soon);
			if (stopped) return;
			if (!still_current()) { stop(); return; }
			if (document.hidden) return;  // 回到页面时再刷
			if (running) { again = true; return; }
			const wait = last + min_ms - Date.now();
			if (wait > 0) { clearTimeout(soon); soon = setTimeout(run, wait); return; }
			running = true;
			last = Date.now();
			try { await refresh(); } catch (error) { /* 下一次再刷 */ }
			running = false;
			if (again) { again = false; return run(); }
			schedule();
		}
		const later = () => { clearTimeout(soon); soon = setTimeout(run, settle_ms); };
		function on_visible() { if (!document.hidden) later(); }
		for (const event of events) {
			const handler = data => { if (!stopped && accept(data)) later(); };
			handlers.push([event, handler]);
			frappe.realtime?.on?.(event, handler);
		}
		document.addEventListener("visibilitychange", on_visible);
		window.addEventListener("focus", on_visible);
		socket?.on?.("connect", on_visible);
		schedule();
		return { stop, now: run };
	};
	p.add_refresh = (page, refresh, label = __("Reload")) =>
		page.add_action_icon("es-line-reload", refresh, "", label)
			.removeClass("btn-default")
			.attr({type: "button", "aria-label": label});
	// 页面文案按前缀整包取。以前每进一页都重新请求一次（同一前缀一个页面里还会取两三次），
	// 而且排在表结构、列表之前，首开多一次来回。现在：同一会话里每个前缀只取一次；启动信息里
	// 有语言包指纹（`pearnly_text_version`，见 `browser_text.py`）时再存进浏览器，刷新、
	// 下次登录都不用再取，语言包一变指纹就变、旧的随即清掉。浏览器存储不可用（隐私模式等）时照旧去取。
	const text_packs = {};
	const TEXT_STORE = "pearnly_text:";
	const stored_pack = (key) => {
		try { return JSON.parse(localStorage.getItem(key) || "null"); } catch (error) { return null; }
	};
	const store_pack = (version, key, pack) => {
		try {
			for (let i = localStorage.length - 1; i >= 0; i -= 1) {
				const name = localStorage.key(i);
				if (name?.startsWith(TEXT_STORE) && !name.startsWith(`${TEXT_STORE}${version}:`)) {
					localStorage.removeItem(name);
				}
			}
			localStorage.setItem(key, JSON.stringify(pack));
		} catch (error) { /* 存不下就算了，下次再取 */ }
	};
	p.text_pack = (prefix) => {
		if (!text_packs[prefix]) {
			const version = frappe.boot.pearnly_text_version;
			const key = `${TEXT_STORE}${version}:${prefix}`;
			const stored = version ? stored_pack(key) : null;
			text_packs[prefix] = stored ? Promise.resolve(stored)
				: p.call("pearnly_core.browser_text.browser_text", { prefix }).then(pack => {
					if (version && pack && Object.keys(pack).length) store_pack(version, key, pack);
					return pack;
				}, error => { delete text_packs[prefix]; throw error; });
		}
		return text_packs[prefix];
	};
	p.texts = async (prefix) => {
		const pack = await p.text_pack(prefix);
		const lang = (frappe.boot.lang || frappe.boot.user.language || "th").slice(0, 2);
		return (key, ...args) => {
			const text = pack[key]?.[lang] || pack[key]?.th || key;
			return text.replace(/\{(\d+)\}/g, (_, i) => String(args[Number(i)] ?? ""));
		};
	};
	// 「静音」是**一位管全站**：官方 `frappe.utils.play_sound` 自己看 `User.mute_sounds`，
	// 而我们自己的提示（音效之外的震动这类）没有那条管道，判断收到这里来 ——
	// 用户 2026-09-23 口径："同时关联静音和不静音，全站"。个人信息页保存后会当场回写这一位。
	p.sounds_muted = () => Boolean(frappe.boot?.user?.mute_sounds);
	// 多行框（备注 / 地址）**默认矮、写不下才长**：高度按内容走，长到上限就自己在框里滚。
	// 一处规则全站生效 —— 皮肤里把默认高度压到两行（`product.css`），这里负责"依次变大"，
	// 所以每个页面、每个弹窗都不用自己接一遍（用户 2026-09-23："地址和备注框默认同比例和
	// 当前在的位置缩小，当字段超出默认尺寸时才自动依次变大"）。
	p.TEXTAREA_MAX_PX = 240;
	p.grow_textarea = element => {
		const node = element?.target ?? element;
		if (!node || node.tagName !== "TEXTAREA") return;
		node.style.height = "auto";
		node.style.height = `${Math.min(node.scrollHeight, p.TEXTAREA_MAX_PX)}px`;
	};
	p.fit_textareas = root => {
		$(root || document).find("textarea.form-control").each((_, node) => p.grow_textarea(node));
	};
	// 敲字、点进去、粘贴之后都过一遍（已有内容的长框，点一下也会长到该有的大小）；
	// 弹窗打开时也过一遍 —— 查看/编辑一张已有单据时，长备注不能只露两行。
	$(document).on("input focus change", "textarea.form-control", event => p.grow_textarea(event.target));
	$(document).on("shown.bs.modal", event => p.fit_textareas(event.target));
	// 官方 `ControlSmallText.make_input()` 里写死了**内联** `height: 150px` ——
	// 皮肤里的高度被它压住，而我们的"内容超出才长"又必须能改这一格。所以照接管
	// `ControlLink` 的同一套做法：继承它、只把那个内联高度去掉，其余（官方的
	// `max_height`、只读呈现、事件）原样留着，交回给皮肤与 `p.grow_textarea`。
	const NativeSmallText = frappe.ui?.form?.ControlSmallText;
	if (NativeSmallText && !NativeSmallText.pearnly_small_text) {
		class SmallText extends NativeSmallText {
			make_input() {
				super.make_input();
				this.$input.css({height: ""});
			}
		}
		SmallText.pearnly_small_text = true;
		frappe.ui.form.ControlSmallText = SmallText;
	}
	// 成功回音。官方那一声是**按动作分**的：保存/提交 → `submit`，删除 → `delete`
	// （框架自己的批量动作就是 `play_sound(action)`，`bulk_operations.js` 里 submit/cancel/delete 各一声），
	// 所以第二个参数留给"这不是提交、是删除"这类场合；默认仍是 submit。
	// ── 上传前缩照片（站点开关 `pearnly_shrink_photos`，默认关；见 `pearnly_core/desk/upload.py`）─────────────
	// 按 EXIF 转正，只缩不放大：长边大于 2400 才缩到 2400（高质量缩放），存成 JPEG 0.92；其余（长边不超过 2400、PDF、
	// 表格、SVG / GIF、浏览器解不开的 HEIC）原样返回。录入工作台与 ERP 采购 / 销售录入的照片上传口用。
	p.shrink_photo = async file => {
		const rule = frappe.boot.pearnly_shrink_photos;
		if (!rule || !(file instanceof Blob) || !/^image\/(jpeg|png|webp|heic|heif)$/i.test(file.type || "")) return file;
		let bitmap = null;
		try {
			bitmap = await createImageBitmap(file, {imageOrientation: "from-image"});
			const long = Math.max(bitmap.width, bitmap.height);
			if (long <= rule.max_long_edge) return file;
			const scale = rule.max_long_edge / long;
			const width = Math.round(bitmap.width * scale), height = Math.round(bitmap.height * scale);
			// 大于目标两倍时先逐次减半，最后一步再缩到目标：一步从 8000 缩到 2400 各浏览器的插值质量不一，容易出锯齿、
			// 糊掉细字（压测窗口 2026-10-01 提醒）。
			const draw = (source, w, h) => {
				const canvas = document.createElement("canvas");
				canvas.width = w; canvas.height = h;
				const context = canvas.getContext("2d");
				context.imageSmoothingEnabled = true;
				context.imageSmoothingQuality = "high";
				context.drawImage(source, 0, 0, w, h);
				return canvas;
			};
			let source = bitmap, w = bitmap.width, h = bitmap.height;
			while (w / 2 >= width && h / 2 >= height) {
				w = Math.round(w / 2); h = Math.round(h / 2);
				source = draw(source, w, h);
			}
			const canvas = draw(source, width, height);
			const blob = await new Promise(resolve => canvas.toBlob(resolve, "image/jpeg", rule.quality));
			if (!blob) return file;
			const name = String(file.name || "photo").replace(/\.[^.]+$/, "") + ".jpg";
			return new File([blob], name, {type: "image/jpeg", lastModified: file.lastModified || Date.now()});
		} catch (error) {
			return file;  // 解不开（如桌面浏览器的 HEIC）：原样上传，服务端照旧处理
		} finally {
			bitmap?.close?.();
		}
	};

	p.success = (message, sound = "submit") => {
		frappe.show_alert({ message: p.escape(message), indicator: "green" });
		frappe.utils.play_sound(sound);
	};

	// ── 导出：小的当场下载，大的排后台、做完推送后下载（服务端 `pearnly_core.exports`）────────────────
	// 原来各页直接 `window.open` / `location.assign` 导出地址，数据多就超时。现在统一走这里：服务端按行数决定，
	// 回的是文件就直接存；回 `{export_job}` 就挂原生黄色提示等着，收到实时推送 `pearnly_export` 就去查一次，
	// 推送连着时每 20 秒、断开时每 5 秒兜底查一次；做完下载并给原生完成提示（带提示音，跟账户静音），失败原生弹窗说原因。
	const EXPORT_API = "pearnly_core.exports.api.";
	const server_message = (payload, status) => {
		try {
			for (const item of JSON.parse(payload?._server_messages || "[]")) {
				const message = JSON.parse(item)?.message;
				if (message) return String(message).replace(/<[^>]*>/g, "").trim();
			}
		} catch (error) { /* 不是标准错误体（网关之类），按状态码说 */ }
		return payload?.exception || `HTTP ${status}`;
	};
	const save_blob = (blob, name) => {
		const url = URL.createObjectURL(blob);
		const link = document.createElement("a");
		link.href = url; link.download = name || "export";
		document.body.appendChild(link); link.click(); link.remove();
		setTimeout(() => URL.revokeObjectURL(url), 1000);
	};
	const file_name = (response) => {
		const header = response.headers.get("Content-Disposition") || "";
		const encoded = /filename\*=UTF-8''([^;]+)/i.exec(header);
		if (encoded) { try { return decodeURIComponent(encoded[1]); } catch (error) { /* 照下面取 */ } }
		return /filename="?([^";]+)"?/i.exec(header)?.[1] || "";
	};
	const wait_export = (job) => new Promise((resolve) => {
		const notice = frappe.show_alert({ message: p.escape(__("Preparing the file")), indicator: "yellow" }, 3600);
		let timer = 0, finished = false;
		const handler = (data) => { if (data?.job === job) check(); };
		const finish = (state) => {
			if (finished) return;
			finished = true;
			clearTimeout(timer);
			frappe.realtime?.off?.("pearnly_export", handler);
			notice?.remove?.();
			if (state?.status === "done") {
				const link = document.createElement("a");
				link.href = `/api/method/${EXPORT_API}download?job=${encodeURIComponent(job)}`;
				document.body.appendChild(link); link.click(); link.remove();
				p.success(__("The file is ready"));
			} else if (!state?.shown) {
				frappe.msgprint({ message: p.escape(state?.error || __("The export could not be created.")),
					indicator: "red" });
			}
			resolve(state);
		};
		const schedule = () => {
			clearTimeout(timer);
			if (!finished) timer = setTimeout(check, frappe.realtime?.socket?.connected ? 20000 : 5000);
		};
		async function check() {
			if (finished) return;
			try {
				const state = await p.call(EXPORT_API + "status", { job });
				if (state?.status === "done" || state?.status === "failed") return finish(state);
			} catch (error) {
				// 查不到（过期 / 没权限）：框架已弹过原因，这里收尾。
				if (error?.exc_type || error?.status === 403) return finish({ status: "failed", shown: true });
			}
			schedule();
		}
		frappe.realtime?.on?.("pearnly_export", handler);
		schedule();
	});
	/** 导出：`method` 是导出接口（照旧的参数，GET 或 POST）。当场给的文件直接存；排了任务就等推送后下载。 */
	p.export_file = async (method, args = {}, { type = "GET" } = {}) => {
		const query = new URLSearchParams();
		for (const [key, value] of Object.entries(args || {})) {
			if (value === undefined || value === null || value === "") continue;
			query.append(key, typeof value === "object" ? JSON.stringify(value) : String(value));
		}
		const options = { credentials: "same-origin",
			headers: { "X-Frappe-CSRF-Token": frappe.csrf_token, Accept: "application/json, */*" } };
		const response = type === "POST"
			? await fetch(`/api/method/${method}`, { ...options, method: "POST", body: query })
			: await fetch(`/api/method/${method}?${query.toString()}`, options);
		const kind = response.headers.get("Content-Type") || "";
		if (response.ok && !kind.includes("application/json")) {
			save_blob(await response.blob(), file_name(response));
			return { status: "done" };
		}
		const body = await response.json().catch(() => null);
		if (response.ok && body?.message?.export_job) return wait_export(body.message.export_job);
		frappe.msgprint({ message: p.escape(server_message(body, response.status)), indicator: "red" });
		return { status: "failed" };
	};

	// ── 识别等候：白底 + 官方转圈（框架样式表自带的 `spinner-border`）+ 一行真实进度 ──────────────
	// 用户 2026-09-30：识别时「白屏加动画」，不再用整屏灰遮罩。盖住这一页的内容区，顶栏与侧栏照常；页头按钮
	// 等候期间停用（识别结果回来前不让提交 / 保存），结束后按原样恢复。文字只写真实的数（份数、已用秒数），不估算。
	// Cowork 录入、ERP 采购 / 销售共用。`elapsed: true` 时下面多一行「已用 N 秒」。
	// `pages: async () => ({done, total})`：每 1.5 秒问一次服务端读了几页，多一行「已识别 N/总 页 · 百分比」
	// （用户 2026-09-30「按页分进度，不要按份」）；问不到或还没开始读就不显示这一行。
	// `image`：刚选的图片在本机的地址（`URL.createObjectURL`），文字下面直接摆原图，不等上传和识别；浏览器显示不了
	// （如桌面浏览器的 HEIC）就不摆。地址由调用方收回。
	p.waiting = (page, text, {elapsed = false, pages = null, image = ""} = {}) => {
		const host = $(page.wrapper).find(".page-body").first();
		host.addClass("pearnly-waiting-host");
		const layer = $(`<div class="pearnly-waiting" role="status" aria-live="polite">
			<div class="spinner-border text-muted" aria-hidden="true"></div>
			<div class="pearnly-waiting-text text-muted"></div></div>`).appendTo(host);
		if (image) $("<img class='pearnly-waiting-image' alt=''>").on("error", event => $(event.target).remove())
			.attr("src", image).appendTo(layer);
		const buttons = $(page.wrapper).find(".page-head button:not([disabled])").prop("disabled", true);
		const started = Date.now();
		let lines = String(text ?? "");
		let read = "";
		const paint = () => {
			const seconds = Math.round((Date.now() - started) / 1000);
			const shown = [lines, read, elapsed ? __("shell.wait.elapsed", [seconds]) : ""].filter(Boolean);
			layer.find(".pearnly-waiting-text").text(shown.join("\n"));
		};
		const timer = elapsed ? setInterval(paint, 1000) : 0;
		let asking = false;
		const poll = pages ? setInterval(async () => {
			if (asking) return;
			asking = true;
			try {
				const now = await pages();
				const done = Number(now?.done) || 0, total = Number(now?.total) || 0;
				if (total) {
					read = __("shell.wait.pages", [Math.min(done, total), total, Math.round(Math.min(done, total) * 100 / total)]);
					paint();
				}
			} catch (error) {
				// 进度只作展示：一时问不到就下次再问
			} finally {
				asking = false;
			}
		}, 1500) : 0;
		paint();
		return {
			update: value => { lines = String(value ?? ""); paint(); },
			done: () => {
				clearInterval(timer);
				clearInterval(poll);
				layer.remove();
				if (!host.children(".pearnly-waiting").length) host.removeClass("pearnly-waiting-host");
				buttons.prop("disabled", false);
			},
		};
	};
	p.dialog = (title, fields, label, action) => {
		const d = new frappe.ui.Dialog({
			title,
			fields,
			primary_action_label: label,
			async primary_action(values) {
				if (d.pearnly_busy) return;
				d.pearnly_busy = true;
				d.disable_primary_action();
				try {
					await action(values, d);
				} catch (error) {
					if (!error?._server_messages && !error?.responseJSON) console.error(error);
				} finally {
					d.pearnly_busy = false;
					d.enable_primary_action();
				}
			},
		});
		// 标记是我们自己的弹窗：框架给下拉浮层的 z-index（2）比 footer（1）高，
		// 短弹窗里最后一行选项正好压在「提交」上 —— 点提交其实点在选项上。
		// 皮肤里把 footer 抬到浮层之上（见 product.css），所以这里只要挂个类。
		d.$wrapper.addClass("pearnly-dialog");
		// 先给控件挂上"数量不留尾零"的显示口径，再 show —— 否则第一遍画出来还是 2.500000。
		Object.values(d.fields_dict || {}).forEach(field => p.tidy_number(field));
		d.show();
		return d;
	};
	p.layout = (parent, fields) => {
		const form = new frappe.ui.FieldGroup({ body: parent, fields });
		form.make();
		Object.values(form.fields_dict).forEach(field => p.tidy_number(field));
		return form;
	};
	// 排序要按**看得见的文字**排。框架的 `_sortRows` 拿单元格的**原始值**直接比大小，而我们很多列的
	// 原始值就是**拼好的 HTML**（商品名带链接、差异带红字）——那样比出来等于按内部单据号排，
	// 用户看到的顺序是乱的；数量若是 `1,204` 这种带千分位的字符串，还会按字符排（`9` > `1,204`）。
	// 所以原始值统一做成"**排序键 + 分隔符 + 要显示的 HTML**"：
	//   · 排序键：能当数字看就按数值（固定 18 位整数 + 6 位小数，负号单独一档，保证负数排在前面），
	//     否则按文字；框架只比字符串，所以键要自己补零；
	//   · 显示仍然是原来那份 HTML，由包装过的 `format` 切回来（框架的单元格标题也取显示内容，
	//     所以悬停提示里不会漏出键）。
	// 这样列头菜单里官方的"升序 / 倒序 / 重置排序"对每一张表都排得对。
	const SORT_SEP = "\u0000";
	const sort_key = (html) => {
		const text = $("<div>").append(html == null ? "" : String(html)).text().trim();
		const cleaned = text.replace(/[,\s\u00a0]/g, "");
		const number = cleaned === "" ? NaN : Number(cleaned);
		if (!Number.isFinite(number)) return `s:${text}`;
		const [whole, fraction] = Math.abs(number).toFixed(6).split(".");
		return `n:${number < 0 ? 0 : 1}${whole.padStart(18, "0")}.${fraction}`;
	};
	p.table = async (parent, columns, data, options = {}) => {
		await frappe.require("report.bundle.js");
		parent.empty();
		if (!data.length) {
			parent.append(frappe.views.QueryReport.prototype.get_no_result_message());
			return { destroy: () => parent.empty() };
		}
		const specs = columns.map((column) =>
			typeof column === "string"
				? { name: column, editable: false, format: p.escape }
				: { editable: false, ...column },
		);
		const rows = data.map((row) => specs.map((spec, index) => {
			const value = row[index];
			const html = spec.format ? spec.format(value, row, spec, data) : (value == null ? "" : String(value));
			return `${sort_key(html)}${SORT_SEP}${html}`;
		}));
		const table = new window.DataTable(parent[0], {
			columns: specs.map((spec) => ({
				...spec,
				// 切回"排序键之外"的那段：就是原来要显示的 HTML。
				format: (value, row, column, all) => {
					const raw = value == null ? "" : String(value);
					const cut = raw.indexOf(SORT_SEP);
					return cut >= 0 ? raw.slice(cut + 1)
						: (spec.format ? spec.format(value, row, column, all) : p.escape(raw));
				},
			})),
			data: rows,
			layout: "fluid",
			noDataMessage: __("No Data"),
			serialNoColumn: false,
			checkboxColumn: false,
			language: frappe.boot.lang,
			translations: frappe.utils.datatable.get_translations(),
			...options,
		});
		// **点列头直接排序**（用户 2026-09-25："点列头我要的是这种，不要那种繁琐的点开、选择"）：
		// 官方只把排序藏在列头右侧那颗小箭头的菜单里。这里沿用**官方自己的排序通道**
		// （菜单里那三项调用的就是同一个 `columnmanager.sortColumn`），把它绑到列头本身：
		// 未排序 → 升序 → 倒序 → 取消（回到原始顺序）。菜单三项保留，官方行为一个字不改。
		// 点在小箭头、列宽拖拽把手或输入控件上时不触发。
		//
		// **先解绑再绑**：宿主被重复建表时（页面重取会重建），旧表的处理器会留在宿主上 ——
		// 点一次列头等于同时驱动好几张**已经销毁**的表，那些表的样式表已经摘掉，于是每张都
		// 抛一次 `Cannot read properties of null (reading 'cssRules')`（实测 4 张表就 4 次）。
		// 命名空间解绑只清本组件自己绑的，不动别人的处理器。
		$(parent).off("click.pearnlyTableSort").on("click.pearnlyTableSort", ".dt-cell--header", function (event) {
			if ($(event.target).closest(".dt-dropdown, .dt-cell__resize-handle, input, select, button").length) {
				return;
			}
			const matched = String(this.className).match(/dt-cell--header-(\d+)/);
			if (!matched) return;
			const index = Number(matched[1]);
			const order = table.datamanager.getColumn(index)?.sortOrder;
			table.columnmanager.sortColumn(index,
				order === "asc" ? "desc" : (order === "desc" ? "none" : "asc"));
		});
		return p.table_menu(table);
	};
	// 列头菜单只留排序三项。官方 datatable 默认还挂"移除栏"和"冻结/解冻"，本项目表格是
	// **只读展示、列由代码定义**：少一列 / 冻一列都不是业务动作，留着只是误导（用户
	// 2026-09-22 口径：没用就删）。做法是沿用官方自己的 `display` 通道 + 关掉粘性列开关，
	// 不改插件源码、不动排序逻辑。
	p.table_menu = table => {
		const items = table?.options?.headerDropdown || [];
		const nodes = table?.dropdownContainer?.firstElementChild?.children || [];
		items.forEach((item, index) => {
			const removable = item.stickyAction === "stick" || item.stickyAction === "unstick"
				|| /\bremoveColumn\b/.test(String(item.action || ""));
			if (!removable) return;
			item.display = "hidden";
			nodes[index]?.classList.add("dt-hidden");
		});
		// 冻结/解冻两项的显隐由列管理组件在"打开菜单"时按列状态切换（`updateStickyDropdownItems`），
		// 所以要把**它**上面这两个索引置 -1（放在 DataTable 上没用：那里没有这两个字段，
		// 实测会白写，菜单里 Freeze 照样冒出来）。
		const columns = table.columnmanager;
		if (columns) columns.stickDropdownIndex = columns.unstickDropdownIndex = -1;
		return table;
	};
	// 表单分几栏：默认两栏。**Frappe 的"栏"是竖着堆字段的**（Column Break 换到下一栏），
	// 所以 `columns=2` 的含义是"两栏、每栏从上往下排"，视觉上就是每排两个字段。
	//
	// 整行控件（HTML / Table / Button / Section）必须落在**新的一节**里，不能只换栏：
	// 只换栏的话它会跟字段并排挤在右边（实测：流水弹窗、收发存二级页的表格都被挤成一条），
	// 所以这里用 Section Break 收口，并且**看下一个字段**决定用哪种换行 ——
	// 恰好填满一栏时如果后面跟着整行控件，就用 Section Break 而不是 Column Break。
	p.field_rows = (fields, columns = 2) => {
		const rows = [];
		let filled = 0;
		const full_width = (field) => ["Section Break", "HTML", "Table", "Button"]
			.includes(field?.fieldtype);
		fields.forEach((field, index) => {
			const type = field.fieldtype;
			if (full_width(field)) {
				if (filled) { rows.push({ fieldtype: "Section Break" }); filled = 0; }
				rows.push(field);
				return;
			}
			if (type === "Column Break") { rows.push(field); filled = 0; return; }
			rows.push(field); filled += 1;
			if (filled === columns) {
				rows.push({ fieldtype: full_width(fields[index + 1]) ? "Section Break" : "Column Break" });
				filled = 0;
			}
		});
		return rows;
	};
	// 每**排** `per_row` 个字段（与上面"每栏 N 个"不是一回事，用户 2026-09-25 口径：
	// "分成一排4组方，视觉对齐"）。做法：一个字段一栏、行与行之间用 Section Break 断开。
	//
	// 为什么末尾要**补空栏**：框架是按"本节里可见栏数"平均分 12 格的（`column.js` 的
	// `resize_all_columns`：`col-sm-<int(12/栏数)>`）。第二排只有 3 个字段时它会被算成 3 栏、
	// 每栏变成 4/12，比第一排的 3/12 宽 —— 两排的竖线就对不齐了。补一栏空的，每排都是 4 栏。
	p.field_grid = (fields, per_row = 4) => {
		const full_width = (field) => ["Section Break", "HTML", "Table", "Button"]
			.includes(field?.fieldtype);
		const groups = [];
		let row = [];
		const close_row = () => { if (row.length) { groups.push(row); row = []; } };
		fields.forEach(field => {
			// 整行控件自己占一整排（收发存那张明细表就是）：先收口当前这一排，再单独放它。
			if (full_width(field)) { close_row(); groups.push(field); return; }
			row.push(field);
			if (row.length === per_row) close_row();
		});
		close_row();
		const out = [];
		groups.forEach((group, index) => {
			if (index) out.push({ fieldtype: "Section Break" });
			if (!Array.isArray(group)) { out.push(group); return; }
			group.forEach((field, position) => {
				if (position) out.push({ fieldtype: "Column Break" });
				out.push(field);
			});
			for (let pad = group.length; pad < per_row; pad += 1) {
				out.push({ fieldtype: "Column Break" });
			}
		});
		return out;
	};
	p.restore_sidebar = () => {
		if (frappe.boot.pearnly_sidebar && frappe.app?.sidebar?.sidebar_title !== frappe.boot.pearnly_sidebar) {
			return frappe.app.sidebar.setup(frappe.boot.pearnly_sidebar);
		}
	};
	p.register = (name, prefix, title, render) => {
		frappe.pages[name].on_page_show = () => p.restore_sidebar();
		frappe.pages[name].on_page_load = async (wrapper) => {
			const page = frappe.ui.make_app_page({
				parent: wrapper,
				title: __(title),
				single_column: true,
			});
			if (!frappe.boot.pearnly_product || !frappe.boot.pearnly_pages?.includes(name)) {
				page.set_title_sub(__("Not Permitted"), "red");
				return;
			}
			try {
				const t = await p.texts(prefix);
				await render(page, t, wrapper);
			} catch (error) {
				console.error(error);
				page.set_title_sub(__("Unable to load this page. Please reload."), "red");
			}
		};
	};

	// ── 二级页面（新建 / 明细）与多级面包屑 ────────────────────────────────────
	// 用户 2026-09-22 口径：**新建是二级面包屑页面，查看是弹窗**；商品、入库、出库统一
	// 这一套。做法与盘点工作台一致 —— 一个 Desk Page 下按路由分段切换内容（第一段是
	// 列表，第二段是二级页面），页面只在首次进入时建一次，之后每次 `show` 按路由重绘。
	p.subroute = (root, ...parts) =>
		"/desk/" + [root, ...parts]
			.filter(part => part !== null && part !== undefined && part !== "")
			.map(encodeURIComponent).join("/");

	// `render(rest, still_current)`：`rest` 是路由第一段之后的全部段（列表时是空数组），
	// `still_current()` 在切走之后返回 false，用来丢掉慢请求的响应。
	p.routed_page = ({ wrapper, root, render }) => {
		let sequence = 0;
		const show = async () => {
			const route = frappe.get_route();
			if (route[0] !== root) return;
			const request = ++sequence;
			await render(rest_of(route, root), () => request === sequence);
		};
		wrapper.on_page_show = () => { p.restore_sidebar(); void show(); };
		$(wrapper).on("hide", () => { ++sequence; });
		return show;
	};
	const rest_of = (route, root) => (route[0] === root ? route.slice(1) : []);
	// 路由段超出这个页面认得的层级（手输的、旧书签）时收敛回上一级，别停在空白页。
	p.redirect_up = (root, ...parts) => frappe.set_route(root, ...parts);

	// 弹窗左下角那颗「全部编辑」：官方 `QuickEntryForm` 用的就是同一个插槽
	// （`dialog.add_custom_action(__("Edit Full Form"), …)`）。用户 2026-09-22 口径：
	// 新建默认走弹窗（快），字段多、要批量或要改细节时点它进二级页面。`carry` 是交给
	// 二级页面的初值；两边在同一个闭包里，不走 `route_options`（不必把值写进 URL）。
	p.edit_all = (dialog, label, route, carry) => {
		dialog.add_custom_action(label, () => {
			dialog.hide();
			carry?.(dialog.get_values(true) || {});
			frappe.set_route(route);
		});
	};
	// 路由切走时把页面恢复成"只剩原生筛选区"的空壳：列表与二级页面共用 page.main，
	// 上一态残留的字段、按钮、指示器都会跟过来（盘点工作台踩过同样的坑）。
	p.clear_page = (page) => {
		page.page_form.detach();
		page.main.empty().append(page.page_form).removeClass("layout-main-list list-view");
		page.clear_fields(); page.fields_dict = {}; page.clear_inner_toolbar();
		// 右上角那排图标也要清：`add_refresh` 每进一次列表就加一颗，不清就会越堆越多
		// （实测收发存页面进了几次之后右上角挂了一排刷新）。用原生 `clear_icons()`。
		page.clear_icons?.();
		page.hide_form();
		page.clear_primary_action(); page.clear_indicator();
	};

	// 多级面包屑：原生 Custom crumb 只画一个节点。这里把整条路径交给框架自己渲染
	// （存在 `breadcrumbs.all` 里，重绘、返回、刷新都能复原），而不是临时插 DOM；窄屏
	// 原生只显示最后一级，所以同时给出 `menu_items`。
	const native_custom = frappe.breadcrumbs.set_custom_breadcrumbs;
	frappe.breadcrumbs.set_custom_breadcrumbs = function (crumb) {
		native_custom.call(this, crumb);
		for (const step of crumb.pearnly_trail || []) {
			this.append_breadcrumb_element(step.route, p.escape(step.label));
		}
		if (crumb.pearnly_trail?.length && crumb.menu_items?.length) {
			// 菜单点击不能再冒泡给 Desk 路由器，否则它会重开当前页并关掉菜单。
			this.$breadcrumbs.children("li:last-child").on("click", (event) => {
				event.preventDefault();
				event.stopPropagation();
			}).find("a").attr("aria-haspopup", "menu")
				.append(frappe.utils.icon("chevron-down", "xs"));
		}
	};
	// `trail` 里带 `route` 的节点可点（父级），最后一个通常不带（就是当前页）。
	p.breadcrumbs = ({ label, route = "", trail = [] }) => {
		const parents = trail.filter(step => step.route);
		frappe.breadcrumbs.add({
			type: "Custom", label, route,
			pearnly_trail: trail,
			menu_items: trail.length ? [
				// 语义键（`nav.home`）而不是英文原句 —— 传 "Home" 会被框架自带译文抢走。
				...(frappe.boot.pearnly_pages?.includes("pearnly-home")
					? [{ label: __("nav.home"), onClick: () => frappe.set_route("pearnly-home") }] : []),
				{ label, onClick: () => frappe.set_route(route || frappe.get_route_str()) },
				...parents.map(step => ({
					label: p.escape(step.label), onClick: () => frappe.set_route(step.route),
				})),
			] : [],
		});
	};
	p.notBuilt = async (page, t) => {
		page.set_title_sub(p.escape(t("shell.notBuilt")));
	};

	if (frappe.boot.pearnly_product) {
		frappe.re_route[""] = frappe.boot.pearnly_landing_url || "/desk/pearnly-home";
		// Use the framework's initialization switches: no search/notification widgets or
		// their keyboard targets. A hidden icon alone would leave the global routes active.
		Object.assign(frappe.boot.desk_settings, {
			search_bar: 0, notifications: 0, form_navigation_buttons: 0,
		});
		const NativeHeader = frappe.ui.SidebarHeader;
		frappe.ui.SidebarHeader = class ProductHeader extends NativeHeader {
			make() {
				super.make();
				// Company names are customer data, never translation keys or HTML.
				const company = p.header_title?.() || frappe.boot.pearnly_company_name || "Pearnly";
				this.wrapper.find(".header-title").text(company).attr("title", company);
				const first = [...new Intl.Segmenter(frappe.boot.lang || "th", { granularity: "grapheme" })
					.segment(company)][0].segment.toLocaleUpperCase();
				this.wrapper.find(".header-logo").empty().append(
					$('<div class="icon-container pearnly-company-initial" aria-hidden="true"></div>').text(first),
				);
			}
			add_navbar_items() {
				// 原生顺序：首页、网站、分隔线、侧栏排序、显示、刷新、分隔线、注销。
				// 上游把"显示"和"注销"挂在通知开关上（产品有意关掉全局搜索/通知），
				// 所以这几项在这里显式给出；行为仍交给原生 SidebarHeader、主题切换、
				// clear_cache 与侧栏编辑态，不自建主题或控件。
				// "侧栏排序"按原生放"编辑侧栏"的位置（第一条分隔线之后、显示之前）。
				const is_dark = frappe.ui.get_current_theme() === "dark";
				this.dropdown_items = [
					...(p.header_menu_items?.() || []),
					{ name: "home", label: __("Home"), icon: "home",
						condition: () => frappe.boot.pearnly_pages.includes("pearnly-home"),
						onClick: () => frappe.set_route("/desk/pearnly-home") },
					{ name: "website", label: __("Website"), icon: "web",
						onClick: () => window.open("https://pearnly.com") },
					{ is_divider: true },
					{ name: "reorder-sidebar", label: __("account.sidebarOrder"), icon: "list",
						condition: () => frappe.boot.pearnly_pages.length > 1,
						onClick: () => p.reorder_sidebar() },
					{ name: "display", label: __("Display"), icon: "monitor",
						items: this.get_display_siblings(is_dark) },
					{ name: "reload", label: __("Reload"), icon: "rotate-ccw",
						onClick: () => frappe.ui.toolbar.clear_cache() },
					{ is_divider: true },
					{ name: "logout", label: __("Logout"), icon: "logout",
						onClick: () => frappe.app.logout() },
				];
			}
			setup_app_switcher() {
				const menu = frappe.ui.create_menu({parent: this.wrapper, menu_items: this.dropdown_items,
					onShow: wrapper => {this.toggle_active(wrapper); p.prepare_header_menu?.(menu);},
					onHide: this.toggle_active, onItemClick: this.toggle_active});
			}
			populate_dropdown_menu() {
				// 标题里那份菜单是逐项渲染的，原生没有"分隔线行"，照搬会多出一行
				// `undefined`。分隔线交给原生 context menu（同一次点击里的另一份）画，
				// 这里只列真正可点的项。
				const items = this.dropdown_items;
				this.dropdown_items = items.filter((item) => !item.is_divider && (!item.condition || item.condition()));
				try {
					super.populate_dropdown_menu();
				} finally {
					this.dropdown_items = items;
				}
			}
		};
	}

	// ── 左下角那颗用户按钮：点开时**左侧那栏整个换成一份两项菜单** ───────────────
	//
	// 原生那颗按钮只有一件事：点了直接进个人信息（`sidebar.html` 里写死
	// `onclick="return frappe.ui.toolbar.route_to_user()"`）。用户 2026-09-23 口径：
	// "我点左下角，左侧菜单就只有个人信息和团队管理" —— 所以点开时把侧栏**条目区**
	// 换成那两项，再点一次（或换路由）用原生 `make_sidebar()` 画回原来的菜单。
	// 条目用框架自己的 `sidebar_item` 模板渲染，图标、hover、折叠态都跟侧栏一致。
	//
	// 项由各产品在 `pearnly_navigation` 的 `user_menu` 里声明，服务端按与侧栏同一套
	// 判据（页面已实现 + 当前用户能进）筛过再进 boot（`desk/navigation.py`）；别人
	// 的菜单里连这一项都没有。
	p.user_menu = (sidebar = frappe.app?.sidebar) => {
		const button = sidebar?.$sidebar?.find(".sidebar-user-button");
		if (!sidebar || !button?.length) return;
		// 产品声明的那几项按声明顺序插在「个人信息」周围：带 `before_profile` 的在它**上面**
		// （ERP 的公司），其余的（团队管理）在它下面。用户 2026-09-23："公司……做在个人信息上方"。
		const declared = frappe.boot.pearnly_user_menu || [];
		const elsewhere = entry => ({ label: __(entry.label), icon: entry.icon || "users",
			route: entry.route, go: () => frappe.set_route(entry.route) });
		const profile = { label: __("My Profile"), icon: "user",
			go: () => frappe.ui.toolbar.route_to_user() };
		const entries = [
			...declared.filter(entry => entry.before_profile).map(elsewhere),
			...(frappe.boot.pearnly_profile_allowed !== false ? [profile] : []),
			...declared.filter(entry => !entry.before_profile).map(elsewhere),
		];
		// 「个人信息 / 团队管理」这两个地方算一个**账号区**：停在里面时左侧就保持这两项，
		// 回到业务页面（首页、销售记录…）才画回业务菜单。用户 2026-09-23 口径："点了
		// 其他菜单（团队管理），左侧菜单发生了变化" —— 进去之后不该变回去。
		const in_account = () => {
			const route = frappe.get_route() || [];
			return entries.some(entry => entry.route && entry.route === route[0])
				|| (route[0] === "Form" && route[1] === "User" && route[2] === frappe.session.user);
		};
		const state = sidebar.pearnly_user_menu = sidebar.pearnly_user_menu
			|| {open: in_account(), bound: false};
		const cards = () => sidebar.$sidebar.find(".body-sidebar-cards");
		// 画成"账号那两项"（原生 `sidebar_item` 模板，图标/hover 与侧栏一致），
		// 每次都重绑一次点击 —— 侧栏重画会把节点换掉。
		const paint_account = () => {
			state.open = true;
			// 当前停在哪一项就给它原生的选中底色（`.active-sidebar`，业务侧栏那套同一个
			// 类与样式）—— 少这个的话点进来两项长得一样，看不出自己在哪。
			const route = frappe.get_route() || [];
			const on_profile = route[0] === "Form" && route[1] === "User"
				&& route[2] === frappe.session.user;
			sidebar.$items_container.empty().append(entries.map(entry => {
				const item = $(frappe.render_template("sidebar_item", {
					item: {label: entry.label, icon: entry.icon, type: "Link", standard: 1},
					path: "", edit_mode: false,
				}));
				if (entry.route ? route[0] === entry.route : on_profile) {
					item.find(".standard-sidebar-item").addClass("active-sidebar");
				}
				return item;
			}));
			sidebar.$items_container.find(".item-anchor").each((index, element) =>
				$(element).on("click", (event) => {
					event.preventDefault();
					// 点进去的这两页本身就在账号区里，菜单保持两项；`in_account()` 守得住。
					entries[index].go();
				}));
			cards().addClass("hidden");
		};
		// 收起 = 让原生重新画一遍业务菜单（我们不自己拼回原来的条目）。
		const show_business = () => {
			state.open = false;
			cards().removeClass("hidden");
			sidebar.make_sidebar();
		};
		// 原生那行 onclick 要摘掉：不然点一下既换菜单又跳走。绑定记号挂在按钮自己身上，
		// 万一那颗按钮被换掉还能重绑（路由监听挂在侧栏实例上，只挂一次）。
		if (!button.data("pearnlyMenu")) {
			button.removeAttr("onclick").data("pearnlyMenu", true).on("click", (event) => {
				event.preventDefault();
				if (state.open) return show_business();
				// 点开时右侧默认落在「个人信息」；已经在账号区里（例如团队管理）就只换菜单。
				if (!entries.length) return;
				if (!in_account()) (entries.includes(profile) ? profile : entries[0]).go();
				paint_account();
			});
		}
		if (!state.bound) {
			state.bound = true;
			frappe.router.on("change", () => {
				if (in_account()) return paint_account();
				if (state.open) show_business();
			});
		}
		// 侧栏每次重画（含框架主动换侧栏、我们自己的 `make_sidebar()`）之后按当前状态画对。
		if (state.open) paint_account();
	};
	// 挂在原生侧栏的 `make_sidebar()` 之后：那颗按钮和条目区都在同一张模板里，而
	// `make_sidebar()` 开头会把条目区清空（挂早了会被清掉，实测）。`p.user_menu()`
	// 自己是幂等的，侧栏每次重建都会重画，这里每次调用都安全。
	const NativeSidebar = frappe.ui.Sidebar;
	frappe.ui.Sidebar = class ProductSidebar extends NativeSidebar {
		make_sidebar(...args) {
			super.make_sidebar(...args);
			p.user_menu(this);
		}
		// 产品站点只认自己那份侧栏：框架会按"这个页面属于哪个模块 / 哪个工作区"来换侧栏
		// （我们的页面都挂在 `Pearnly Core` 模块下，`pearnly-team` 这种**被移出侧栏**的
		// 页面就会中招 —— 2026-09-23 实测：点进去之后左侧换成一份二十来项的平铺原生清单，
		// 上面还带着 `团队管理`，跟"菜单只放在侧栏里"正好相反）。所以在 `setup()` 这一层
		// 把目标钉成我们这一份：模块/工作区侧栏留给没有外壳的裸 Desk。
		setup(workspace_title) {
			super.setup(frappe.boot?.pearnly_sidebar || workspace_title);
		}
	};

	// ── 上传框只留「我的设备 / 摄像头」（用户 2026-09-23 口径）───────────────────
	//
	// 框架的上传器默认给四颗按钮：我的设备 / 库（本站已有的文件）/ 链接（外部网址）/
	// 摄像头。我们收的是**当场拍的照片或自己选的文件**："库"会让人把别的单据的附件
	// 再挂一次（一串单据挂同一份图，对不上是哪张），"链接"存的是别人服务器上的地址
	// （对方删了就断、我们也没有留底）。所以这两颗全站去掉，只留另外两颗。
	//
	// 为什么挂 setter 而不是直接替换：`frappe.ui.FileUploader` 是**懒加载**的
	// （`frappe/upload.js` 里那句 `frappe.require("file_uploader.bundle.js")` 是异步的），
	// 我们这段脚本可能跑在它前面、也可能跑在它后面。挂上存取器之后，框架**什么时候**
	// 赋值都会被换成"固定关掉这两项"的子类；直接替换的话，框架那次赋值会把补丁盖掉。
	// 覆盖范围是**全站**：不但我们的六处调用，框架自己的附件侧栏、文件列表、表单附件、
	// 沟通记录里的上传框也一起收紧（那些地方我们改不到）。
	(() => {
		const minimal = Native => {
			if (typeof Native !== "function" || Native.pearnly_minimal_uploader) return Native;
			const Patched = class extends Native {
				constructor(options = {}) {
					super({...options, allow_web_link: false, disable_file_browser: true});
				}
			};
			Object.defineProperty(Patched, "pearnly_minimal_uploader", {value: true});
			return Patched;
		};
		let current = minimal(frappe.ui.FileUploader);
		try {
			Object.defineProperty(frappe.ui, "FileUploader", {
				configurable: true, enumerable: true,
				get: () => current,
				set: value => {current = minimal(value);},
			});
		} catch (error) {
			// 理论上不会走到这里（`frappe.ui` 是我们自己的对象）。真发生了也把当前那一个
			// 换掉，并留一条日志 —— 宁可少一层保险，也不要静默地什么都没做。
			frappe.ui.FileUploader = current;
			console.warn("pearnly: uploader options not hooked", error);
		}
	})();

	$(document).on("app_ready", () => {
		if (!frappe.boot.pearnly_product) return;
		document.body.classList.add("pearnly-product-desk");
		// app_ready fires inside Application construction, before frappe.app is assigned.
		frappe.Application.prototype.logout = async () => {
			const t = await p.texts("account.");
			frappe.confirm(
				p.escape(t("account.signOutConfirm")),
				async () => {
					const result = await p.call("pearnly_core.identity.sign_out", {}, "POST");
					location.assign(result.redirect_url);
				},
				null,
				t("account.signOut"),
				__("Cancel"),
			);
		};
	});
})();
