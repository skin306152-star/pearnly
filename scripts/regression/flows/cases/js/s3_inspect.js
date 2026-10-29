// 库里读回（exec s3-inspect）：来由、码、只一件、行来源、实盘、停用自动启用与时间线、Express 归属
const {found} = exec_out || {};
const {count, express_count, desk_name} = s3();
const one = list => Array.isArray(list) && list.length === 1 ? list[0] : null;
const desk = one(found.desk), express = one(found.express), disabled = one(found.disabled.product);
const line = (p, c) => p && (p.lines || []).find(l => l.count === c);
const checks = {
  desk_single: !!desk, desk_origin: desk && desk.origin === "stocktake" && desk.origin_name === count && desk.origin_doctype === "ERP Stock Count",
  desk_code: desk && (desk.barcode === s3().codes.desk || desk.qr_payload === s3().codes.desk) && /^P-/.test(desk.code || ""),
  desk_name_kept: desk && desk.product_name === desk_name, desk_not_express: desk && !desk.express_company,
  desk_row: desk && line(desk, count) && line(desk, count).row_origin === "new" && Number(line(desk, count).counted_qty) === 3,
  count_posted: desk && desk.counts[count] && desk.counts[count].status !== "Active",
  disabled_active: disabled && Number(disabled.is_active) === 1,
  disabled_row: disabled && line(disabled, count) && line(disabled, count).row_origin === "reactivated",
  disabled_timeline: (found.disabled.comments || []).some(c => String(c).includes(count)),
  express_company: express && express.express_company === "${company}" && express.origin === "stocktake" && express.origin_name === express_count,
};
return {ok: Object.values(checks).every(Boolean), checks, desk, disabled: disabled && {is_active: disabled.is_active, lines: disabled.lines}, comments: found.disabled.comments};
