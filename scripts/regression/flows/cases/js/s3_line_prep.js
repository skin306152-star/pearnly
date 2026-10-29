// 手机页准备（exec s3-prepare）：本轮的码；给 LINE 页建一份盘点
const count = await newCount("line");
s3save({...exec_out, count});
try { localStorage.setItem("flows_count", count); } catch (e) { /* 下一步取不到就判不通过 */ }
return {ok: !!count && !!(exec_out && exec_out.codes), count};
