// 读回（exec s3-inspect）：手机建的商品来由是这份盘点、行来源「新发现」实盘 2；停用商品行来源「停用启用」
const {found} = exec_out || {};
const {count} = s3();
const line = found.line && found.line.length === 1 ? found.line[0] : null;
const row = p => p && (p.lines || []).find(l => l.count === count);
const disabled = (found.disabled.product || [])[0];
const checks = {single: !!line, origin: line && line.origin === "stocktake" && line.origin_name === count,
  row: line && row(line) && row(line).row_origin === "new" && Number(row(line).counted_qty) === 2,
  disabled_row: disabled && row(disabled) && row(disabled).row_origin === "reactivated"};
return {ok: Object.values(checks).every(Boolean), checks, line};
