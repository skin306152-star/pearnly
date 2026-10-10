"""智能体引擎目录（ADR-019）：能接哪些厂商、各家的密钥格式、默认模型与依赖，全平台只在这里登记一次。

- 对话站点、租户管理后台（下拉选项与保存校验）、开通步骤（写站点配置前的校验）、运行镜像的依赖检查
  都读这一份，不各自写死厂商名、密钥前缀或模型。
- 新接一家：在本目录加一个适配器模块（实现 `base.Adapter`），在 `ENGINES` 加一行，运行镜像装上它的 SDK；
  其余各层自动跟上。对话页只认统一的对话事件（见 `base`），不随厂商变。
- 纯数据与纯函数：不依赖 Frappe、网络或 SDK。控制面、开通步骤与离线测试直接加载。
"""
import re
from dataclasses import dataclass

MODEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@-]{0,127}$")


@dataclass(frozen=True)
class Engine:
    code: str  # 存进站点配置与租户记录的值，登记后不改
    label: str  # 管理后台下拉里显示的厂商产品名（专有名词，不翻译）
    adapter: str  # 适配器类的点路径，用时才导入：没用到的厂商 SDK 不必加载
    key_pattern: str  # 密钥格式，只挡明显填错；真伪由厂商接口判定
    default_model: str  # 租户没指定模型时用
    package: str  # 运行镜像里装的 SDK（锁定版本，见 frappe.Dockerfile）


ENGINES = (
    Engine(code="anthropic-managed-agents", label="Claude Managed Agents",
           adapter="pearnly_core.assistant.engines.anthropic_managed_agents.Adapter",
           key_pattern=r"sk-ant-[A-Za-z0-9_-]{20,}", default_model="claude-opus-5-5",
           package="anthropic==1.8.0"),
)


class EngineError(ValueError):
    pass


def options() -> list[dict]:
    """管理后台下拉选项。"""
    return [{"value": e.code, "label": e.label} for e in ENGINES]


def get(code) -> Engine:
    found = next((e for e in ENGINES if e.code == str(code or "").strip()), None)
    if not found:
        raise EngineError("unknown engine")
    return found


def check(code, api_key, model="") -> tuple[str, str, str]:
    """校验一组智能体设置，返回去掉空白后的 (引擎, 密钥, 模型)；模型留空表示用该引擎的默认模型。"""
    engine = get(code)
    key, model = str(api_key or "").strip(), str(model or "").strip()
    if not re.fullmatch(engine.key_pattern, key):
        raise EngineError("the API key does not match the selected engine")
    if model and not MODEL.match(model):
        raise EngineError("invalid model")
    return engine.code, key, model
