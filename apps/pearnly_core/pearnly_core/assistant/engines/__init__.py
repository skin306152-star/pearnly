"""智能体引擎目录（ADR-019）：能接哪些厂商、各家的密钥格式、默认模型与依赖，全平台只在这里登记一次。

- 对话站点、租户管理后台（引擎与模型下拉、保存校验）、开通步骤（写站点配置前的校验）、运行镜像的依赖检查
  都读这一份，不各自写死厂商名、密钥前缀或模型。
- 新接一家：在本目录加一个适配器模块（实现 `base.Adapter`），在 `ENGINES` 加一行，运行镜像装上它的 SDK；
  其余各层自动跟上。对话页只认统一的对话事件（见 `base`），不随厂商变。
- 纯数据与纯函数：不依赖 Frappe、网络或 SDK。控制面、开通步骤与离线测试直接加载。
"""
import re
from dataclasses import dataclass

# 有计价分档的模型，对话的输入一到分档线的这个比例就接续到新上下文（见 `sessions`）。留足余量：
# 一轮里读大文件、表格、图片会一下子加很多，而一轮进行中无法再压缩。
CONTEXT_SHARE = 0.6


@dataclass(frozen=True)
class Engine:
    code: str  # 存进站点配置与租户记录的值，登记后不改
    label: str  # 管理后台下拉里显示的厂商产品名（专有名词，不翻译）
    adapter: str  # 适配器类的点路径，用时才导入：没用到的厂商 SDK 不必加载
    key_pattern: str  # 密钥格式，只挡明显填错；真伪由厂商接口判定
    models: tuple[str, ...]  # 后台可选的模型（经该厂商接口实测可用），第一个是默认
    package: str  # 运行镜像里装的 SDK（锁定版本，见 infrastructure/images/frappe.Dockerfile）
    tiers: tuple[tuple[str, int], ...] = ()  # (模型, 单次请求输入超过多少 token 就按更高价计费)


ENGINES = (
    Engine(code="anthropic-managed-agents", label="Claude Managed Agents",
           adapter="pearnly_core.assistant.engines.anthropic_managed_agents.Adapter",
           key_pattern=r"sk-ant-[A-Za-z0-9_-]{20,}",
           # 2026-10-10 用建智能体实测：这几个都被接受，不存在的模型编号会被拒。
           models=("claude-opus-5-5", "claude-fable-5-1", "claude-sonnet-5-5", "claude-haiku-5-5"),
           # 官方价格页 2026-10-10：Haiku 5.5 单次请求输入（含缓存读写）超过 10 万 token，整次按 5 倍价计费；
           # 其余几个在 1M 上下文内同价。引擎自带的压缩要到接近 1M 才触发，所以分档由站点自己控制。
           tiers=(("claude-haiku-5-5", 100_000),),
           package="anthropic==1.8.0"),
)


class EngineError(ValueError):
    pass


def options() -> list[dict]:
    """管理后台下拉选项：引擎及其可选模型（第一个是默认）。"""
    return [{"value": e.code, "label": e.label, "models": list(e.models)} for e in ENGINES]


def default_model(code) -> str:
    return get(code).models[0]


def get(code) -> Engine:
    found = next((e for e in ENGINES if e.code == str(code or "").strip()), None)
    if not found:
        raise EngineError("unknown engine")
    return found


def context_limit(code, model) -> int | None:
    """该模型的对话输入到多少 token 就该接续到新上下文；没有计价分档返回 None（交给引擎自带的压缩）。"""
    tier = dict(get(code).tiers).get(model)
    return int(tier * CONTEXT_SHARE) if tier else None


def check(code, api_key, model="") -> tuple[str, str, str]:
    """校验一组智能体设置，返回去掉空白后的 (引擎, 密钥, 模型)；模型须是该引擎登记的，留空表示用默认模型。"""
    engine = get(code)
    key, model = str(api_key or "").strip(), str(model or "").strip()
    if not re.fullmatch(engine.key_pattern, key):
        raise EngineError("the API key does not match the selected engine")
    if model and model not in engine.models:
        raise EngineError("model not offered by the selected engine")
    return engine.code, key, model
