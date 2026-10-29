// 后台发送（exec express-send）：有还没 Express 编码的新商品 → 停在「Express 中没有这件商品」交人，不写 TK
const out = exec_out || {};
return {ok: out.count === s3().express_count && out.express_state === "held" && out.express_reason === "express.item_unmapped", out};
