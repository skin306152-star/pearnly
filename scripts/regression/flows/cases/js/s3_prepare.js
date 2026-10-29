// 准备（exec s3-prepare）：本轮的码、一件停用商品
s3save({...exec_out, count: null});
return {ok: !!(exec_out && exec_out.codes), exec_out};
