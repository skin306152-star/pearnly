"""智能体入口的纯约定（ADR-019）。不依赖 Frappe、网络；离线测试直接加载本文件。"""
import hashlib
import hmac
import re
from urllib.parse import urlencode

LOGIN_MAX = 254
SITE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$")
ISSUER = re.compile(r"^https?://[^\s/?#]+(?::\d+)?/realms/[A-Za-z0-9_-]+$")
# 入口支持的语言（与对话页一致）；浏览器给的其他语言一律用泰文。Keycloak 的中文代码是 zh-CN。
LANGS = ("th", "zh", "en")
DEFAULT_LANG = "th"
KEYCLOAK_LOCALE = {"th": "th", "zh": "zh-CN", "en": "en"}
CLIENT_ID = "pearnly-ai"
TOKEN_CONTEXT = b"pearnly-assistant-entry-v1"


class ContractError(ValueError):
    pass


def login(value) -> str:
    """用户名或邮箱：去空白、小写（两个账号池的用户名与邮箱都不区分大小写）。"""
    text = str(value or "").strip().lower()
    if not text or len(text) > LOGIN_MAX or any(c.isspace() for c in text):
        raise ContractError("invalid login")
    return text


def site(value) -> str:
    text = str(value or "").strip().lower()
    if not SITE.match(text):
        raise ContractError("invalid site")
    return text


SESSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{3,127}$")


def session(value) -> str:
    """引擎的远端会话编号（同 pearnly_core.assistant.contracts.session_id 的规则）。"""
    text = str(value or "").strip()
    if not SESSION.match(text):
        raise ContractError("invalid session")
    return text


def issuer(value) -> str:
    text = str(value or "").strip().rstrip("/")
    if not ISSUER.match(text):
        raise ContractError("invalid issuer")
    return text


def lang(value) -> str:
    """把 Accept-Language / Cookie 里的语言归到入口支持的三种。"""
    for part in str(value or "").replace("_", "-").lower().split(","):
        code = part.split(";")[0].strip().split("-")[0]
        if code in LANGS:
            return code
    return DEFAULT_LANG


def entry_token(root: str) -> str:
    """计费服务给入口站点的查询令牌：由计费服务的根密钥派生，只能用于入口查询。"""
    if not root:
        raise ContractError("missing root")
    return hmac.new(root.encode(), TOKEN_CONTEXT, hashlib.sha256).hexdigest()


def ordered_issuers(found, preferred="") -> list[str]:
    """账号所在的账号池（去重）；上次用过的排第一。同一账号可能在两个池都有。"""
    seen = []
    for item in found:
        if item and item not in seen:
            seen.append(item)
    if preferred in seen:
        seen.remove(preferred)
        seen.insert(0, preferred)
    return seen


def authorize_url(issuer_url: str, *, redirect_uri: str, state: str, nonce: str, challenge: str,
                  login_hint: str = "", locale: str = "", silent: bool = False) -> str:
    params = {"client_id": CLIENT_ID, "response_type": "code", "scope": "openid", "redirect_uri": redirect_uri,
              "state": state, "nonce": nonce, "code_challenge": challenge, "code_challenge_method": "S256"}
    if login_hint:
        params["login_hint"] = login_hint
    if locale:
        params["ui_locales"] = KEYCLOAK_LOCALE.get(locale, locale)
    if silent:
        params["prompt"] = "none"
    return f"{issuer(issuer_url)}/protocol/openid-connect/auth?{urlencode(params)}"


def site_entry_url(site_name: str, locale: str = "", picked: bool = False) -> str:
    """进入某个公司站点的智能体：交给站点自己的统一登录口，账号池里已有登录状态时不再要密码。
    `locale` 是入口页这次显示的语言，`picked` 表示是用户点选的（不是按浏览器 / 系统语言自动显示的）。
    站点按 pearnly_core.account.ui_language 的规则用它：点选的，或这台设备第一次登录，就成了这台设备的界面语言
    并写进账号；这台设备已记住语言、这次又没点，就照旧。"""
    url = f"https://{site(site_name)}/api/method/pearnly_core.identity.begin_login?redirect_to=/assistant"
    if locale not in LANGS:
        return url
    return url + f"&locale={locale}" + ("&picked=1" if picked else "")
