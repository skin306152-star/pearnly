// 过账（库存岗位）→ 新建的两件、停用那件按实盘入账
const {count} = s3();
const posted = await quietCall(C + "api_stock_count_post", {name: count});
const stock = await call(C + "api_stock", {warehouse: "${seed.count_warehouse}", include_zero: 1, limit: 2000}, "GET");
return {ok: !!posted && !posted.error, posted: posted && (posted.error || posted.status || posted.name), stock_rows: (stock || []).length};
