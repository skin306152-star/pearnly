"""调 Claude：① 画面核对（一张截图 + 期望 → 结构化 JSON）；② 电脑操作循环（computer_toolset_20260801）。

写法按 claude-api 技能 2026-10-26 核对：
- Opus 5.5 / Haiku 5.5 在 Claude API 上电脑操作只认工具集 ``{"type": "computer_toolset_20260801"}``（不带 name、不带屏幕尺寸）；
  模型的调用是 name = 成员名（screenshot / left_click / type / key / zoom …）的 tool_use 块，带 ``toolset_name: "computer"``，
  一轮可能多个，按顺序执行，每个回一个 tool_result 并带回 ``toolset_name``；遇错即停，后面的回「没执行」。
- Opus 5.5 思考关不掉，只用 effort 调；Haiku 5.5 在 effort ≤ high 时可以关思考（核对时关）。
- 旧截图用服务端上下文编辑（clear_tool_uses_20250919，beta context-management-2025-06-27）清，不在本机删历史
  （本机删会让之前的思考块失效）。
- 每张单单独一段对话；系统提示加缓存标记（同一批单共享），另开顶层自动缓存（同一段对话里旧轮次按缓存读价计）。
- 2026-10-26 用免费的 count_tokens 实测（两个模型一样）：一次画面核对约 1,874 输入 token；一张 1280×720 截图约 1,199；
  接手首轮（工具集定义 + 系统提示 + 目标 + 一张图）约 6,755。
- 没开服务端拒答兜底（fallbacks）：试点要测的就是这两个模型本身，遇到拒答按「要人处理」记。
"""
from __future__ import annotations

import base64
import json
import time
from typing import Dict, List, Optional, Tuple

from . import pricing

TOOLSET = {"type": "computer_toolset_20260801"}
FINISH_TOOL = {
    "name": "finish",
    "description": "结束这次任务。resolved = 目标已达到；need_human = 需要人来处理（说明原因）；escalate = 你处理不了，交给更强的模型。",
    "strict": True,
    "input_schema": {"type": "object", "additionalProperties": False, "required": ["status", "note"],
                     "properties": {"status": {"type": "string", "enum": ["resolved", "need_human", "escalate"]},
                                    "note": {"type": "string"}}},
}
CONTEXT_EDIT = {"edits": [{"type": "clear_tool_uses_20250919", "trigger": {"type": "input_tokens", "value": 40000},
                           "keep": {"type": "tool_uses", "value": 4},
                           "clear_at_least": {"type": "input_tokens", "value": 8000}}]}
BETAS = ["context-management-2025-06-27"]

CHECK_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["state", "what_i_see", "dialog_text", "message_no", "lines_seen", "total_seen", "docnum_seen"],
    "properties": {
        "state": {"type": "string", "enum": ["as_expected", "dialog", "wrong", "unsure"]},
        "what_i_see": {"type": "string"},
        "dialog_text": {"type": "string"},
        "message_no": {"type": "string"},
        "lines_seen": {"type": "integer"},
        "total_seen": {"type": "string"},
        "docnum_seen": {"type": "string"},
    },
}

CHECK_SYSTEM = """你在核对泰国会计软件 Express（Windows 老式界面，泰文）当前画面是否符合期望。只看图说事实，不猜。
- 有弹窗（消息框、选择框）就回 state=dialog，dialog_text 抄弹窗文字，message_no 填消息号（如 4079，没有就空）。
- 只按「期望」那一句核对；草稿单只是参考。期望没提到的部分（例如还没轮到的明细、还没保存）不算不符。
- 画面和期望不符回 wrong；看不清回 unsure；相符回 as_expected。
- lines_seen：明细表格里已有内容的行数（不算空行）；total_seen：画面上的含税合计（看不到就空）；
  docnum_seen：单号栏的内容（如 RRNEW 或 RR0000012，看不到就空）。what_i_see 用一句中文。"""

CU_SYSTEM = """你在一台 Windows 电脑上操作泰国会计软件 Express（服务器版 1.0.1.94，界面泰文），替会计照「草稿单」录单。
草稿单的会计口径已定，你只管照录，不改科目、不改金额、不自作主张。

画面：截图是 1280×720（真实屏幕 1920×1080 按比例缩小），坐标用截图里的坐标。字小可以用 zoom 看局部。

Express 操作要点（实录）：
- 主菜单 Alt+1…Alt+8，再按数字：赊购 Alt+1→4；现购 Alt+1→2→1；赊销 Alt+2→4；现销 Alt+2→2→1。菜单键要连着按完。
- 单据窗口最大化后工具栏：新建 (23,69)、编辑 (53,69)、取消 (153,69)、保存 (183,69)（截图坐标）。
- 新单单号显示「前缀NEW」，保存后才变正式号。
- 明细：品码 → 回车（带出品名）→ 仓（默认 01，要改先 Home、Shift+End 再输）→ 回车 → 数量 → 回车 → 弹「单位」框按回车（ตกลง）
  → 单价 → 回车 ×3，这一行结束、开下一行。录完最后多出的空行按 Esc 退掉再保存。
- 日期格式 日/月/佛历两位年，如 25/10/69。数字不带千分位。
- 泰文直接用 type 输入（程序会自动走剪贴板 + Shift+Insert）；不要用 Ctrl+V。
- 常见弹窗：4079（客户没买过此品，要自己填价）、4107（价格表为 0）、605（库存会变负，提醒）→ 都按回车（ตกลง）继续。
  4224 / 4091（负库存被拦）→ 不要硬录，finish(need_human)。

安全规矩（必须遵守）：
- 只操作 Express。不点 Express 以外的任何东西，不关 Express，不按 Alt+F4，不切换账套 / 公司，不进设置。
- 看到「Error -70 … Reading File」这种网络读文件错误框：什么都别点，立刻 finish(need_human)。
  注意别和普通提示框混淆：标题是「Message#. 70」、内容「วันที่ไม่อยู่ในงวด」（日期不在会计期间）是普通校验提示，按 ตกลง 后把日期改对即可。
- 改格子内容时先全选（Home 再 Shift+End）再输入，不要在原有内容中间插字。
- 在某些状态下按 Esc 或「取消」会直接保存半张单：不确定时先截图看清再按。
- 除非任务里说要保存，否则不要点保存。不要重复保存同一张单。
- 做完（或确认做不到）一定调用 finish。"""


def png_block(png_b64: str) -> Dict:
    return {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": png_b64}}


class Claude:
    """真调 API。密钥由调用方从本机配置读出后传进来，这里不打印、不落盘。"""

    def __init__(self, api_key: str):
        import anthropic

        self.client = anthropic.Anthropic(api_key=api_key, max_retries=3)

    def checkpoint(self, model: str, png_b64: str, prompt: str, expect: Dict) -> Tuple[Dict, object]:
        kwargs = {}
        if model == pricing.HAIKU:
            kwargs["thinking"] = {"type": "disabled"}
        resp = self.client.messages.create(
            model=model, max_tokens=2000, system=CHECK_SYSTEM,
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": CHECK_SCHEMA}},
            messages=[{"role": "user", "content": [png_block(png_b64), {"type": "text", "text": prompt}]}],
            **kwargs)
        if resp.stop_reason == "refusal":
            return {"state": "unsure", "what_i_see": "模型拒答", "dialog_text": "", "message_no": "",
                    "lines_seen": 0, "total_seen": "", "docnum_seen": ""}, resp.usage
        text = "".join(b.text for b in resp.content if b.type == "text")
        return json.loads(text), resp.usage

    def cu_turn(self, model: str, messages: List[Dict], effort: str, max_tokens: int = 8000):
        return self.client.beta.messages.create(
            model=model, max_tokens=max_tokens,
            system=[{"type": "text", "text": CU_SYSTEM, "cache_control": {"type": "ephemeral"}}],
            tools=[TOOLSET, FINISH_TOOL], messages=messages, output_config={"effort": effort},
            thinking={"type": "adaptive"}, context_management=CONTEXT_EDIT, betas=BETAS,
            cache_control={"type": "ephemeral"})  # 自动缓存对话前缀：每轮重发的旧截图按缓存读价计


def blocks(resp) -> List[Dict]:
    """把响应内容块变成可读的字典（只用于判断和记录；往回传历史时用原对象，保证原样）。"""
    out = []
    for b in resp.content:
        d = b.model_dump() if hasattr(b, "model_dump") else dict(b)
        out.append(d)
    return out


def usage_of(resp):
    return resp.usage if not isinstance(resp, dict) else resp["usage"]


def prompt_size(usage) -> int:
    return pricing.prompt_tokens(pricing.normalize(usage))


def decode_png(data: str) -> bytes:
    return base64.b64decode(data)


def now() -> float:
    return time.monotonic()


def finish_from(content: List[Dict]) -> Optional[Dict]:
    for b in content:
        if b.get("type") == "tool_use" and b.get("name") == "finish":
            return b.get("input") or {}
    return None
