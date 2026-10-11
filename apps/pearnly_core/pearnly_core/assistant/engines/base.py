"""引擎适配器接口（ADR-019）：对话站点只通过这几项操作使用厂商服务。

统一对话事件：站点与对话页之间、站点与适配器之间只走一种事件形状——沿用 Managed Agents 事件格式
（`user.message` / `agent.message` / `agent.thinking` / `agent.tool_use` / `agent.tool_result` /
`agent.custom_tool_use` / `user.tool_confirmation` / `user.custom_tool_result` / `user.interrupt` /
`session.status_*`，每条带唯一 `id`）。别家厂商的适配器负责双向翻译成这一种；对话页、工具派发与
会话归属都不因换厂商而改。

适配器只管远端：会话归谁、能不能看由站点数据库判定（`sessions.owned`），工具由站点按当前用户执行
（`tools.run`），这里都不碰。纯接口，不依赖 Frappe。
"""
import abc
from collections.abc import Callable, Iterable, Iterator


class Adapter(abc.ABC):
    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model

    @abc.abstractmethod
    def prepare(self, brief: dict) -> dict:
        """按智能体说明（`definition.brief`）在厂商账号里备好所需资源，返回可缓存的纯 JSON 句柄。
        要幂等：同一说明重复调用不新建；说明变了就更新。"""

    @abc.abstractmethod
    def open(self, handle: dict, metadata: dict) -> tuple[str, str]:
        """新建一个会话，返回 (会话编号, 状态)。"""

    @classmethod
    def wake(cls, event: dict) -> str | None:
        """引擎回调（已验签的通知）若意味着某个会话可能在等工具结果，回那个会话编号；其余回 None。
        厂商没有回调就一直回 None（只靠推送线程与页面兜底）。"""
        return None

    def metadata(self, conversation: str) -> dict:
        """建会话时记下的附带信息（`pearnly_site` 等），引擎回调据此找到会话属于哪个站点。不支持就回空。"""
        return {}

    def refresh(self, conversation: str, brief: dict) -> bool:
        """把已有会话的工具换成当前说明里的（新接的工具老对话也能用）。厂商不支持就不做，返回 False。"""
        return False

    @abc.abstractmethod
    def status(self, conversation: str) -> str:
        """`idle` / `running` / …"""

    @abc.abstractmethod
    def rename(self, conversation: str, title: str) -> None: ...

    @abc.abstractmethod
    def archive(self, conversation: str) -> None:
        """停下正在进行的回合并归档。"""

    @abc.abstractmethod
    def delete(self, conversation: str) -> None:
        """删除；远端已经不存在算成功。"""

    @abc.abstractmethod
    def events(self, conversation: str, newest_first: bool = False) -> Iterable[dict]:
        """统一对话事件。可以是惰性的：增量拉取只读到锚点为止。"""

    @abc.abstractmethod
    def stream(self, conversation: str, opened: Callable[[], None]) -> Iterator[dict]:
        """实时事件流：接上后先调一次 `opened()`，之后每产生一条统一对话事件就交出一条（只含接上之后的）。
        可以夹带回复的逐字预览（`event_start` / `event_delta`，不入事件记录、没有自己的编号）。
        由站点的转发线程消费（见 `relay`），断线或超时直接结束迭代 / 抛错，由调用方决定是否重接。"""

    @abc.abstractmethod
    def post(self, conversation: str, events: list[dict]) -> None:
        """发出用户一侧的统一事件（消息、批准、工具结果、打断）。"""

    @abc.abstractmethod
    def attach(self, conversation: str, filename: str, content: bytes, mime: str) -> dict:
        """把附件交给会话，返回 `{"file_id", "path"}`：`path` 是智能体读取它的位置，会随消息告诉智能体。"""

    @abc.abstractmethod
    def files(self, conversation: str) -> list[dict]:
        """本会话的文件：`{"id", "filename", "size_bytes", "mime_type", "downloadable", "created_at",
        "kind": "upload" | "output"}`。"""

    @abc.abstractmethod
    def read_file(self, conversation: str, file_id: str) -> bytes:
        """文件内容。调用方已核对过文件属于这个会话。"""
