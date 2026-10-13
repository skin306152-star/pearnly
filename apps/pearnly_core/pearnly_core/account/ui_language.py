"""对话页（CC）的界面语言：多产品共用（登录 / 账号），纯规则，不依赖框架与 IO。

用户 2026-10-13：界面默认跟这台设备（系统）的语言。泰文 Windows 的新电脑装好 CC，登录页按系统语言显示泰文，
登录后却整页变成账号里以前存的中文。现在只有一条规则，先后是：

1. 用户在这台设备上自己选的语言（对话页左下角「语言」，或这次登录前在登录页上点的）：记在这台设备上，也存成账号语言；
2. 这台设备还没记住语言时（在这台设备上第一次登录）：登录页这次显示的语言，不论是点的还是按系统语言自动显示的，
   同样记在这台设备上、存成账号语言（服务端在后台发的提醒、简报、LINE 文案随账号语言，也就跟着它）；
3. 这台设备已经记住的语言；
4. 都没有：账号语言 → 系统（浏览器）语言 → 英文。

对话页自己的请求带着页面当前的语言，服务端这次请求的文案按它（`preferences.use_page_language`）；
后台、异步的文案仍按账号语言。
"""
UI_LANGUAGES = ("th", "zh", "en")  # 对话页有的语言（frontend/assistant/src/i18n.ts）
FALLBACK = "en"


def normalize(tag) -> str:
    """一个语言标签归到对话页的语言：th-TH → th，zh-CN / zh-TW / zh_Hant → zh，en-US → en；别的给空串。"""
    code = str(tag or "").strip().replace("_", "-").split("-")[0].lower()
    return code if code in UI_LANGUAGES else ""


def from_accept_language(header) -> str:
    """浏览器 / 系统语言（Accept-Language，按用户排的先后）里第一个对话页有的语言；一个都没有给空串。"""
    for part in str(header or "").split(","):
        code = normalize(part.split(";")[0])
        if code:
            return code
    return ""


def sign_in_choice(device, shown, picked=False) -> str:
    """这次登录给这台设备定下的新语言（同时存成账号语言）；空串表示没有新选择，照旧用设备记住的与账号里的。

    device：这台设备已记住的；shown：登录页这次显示的；picked：shown 是用户在登录页上点选的。"""
    device, shown = normalize(device), normalize(shown)
    if shown and (picked or not device):
        return shown
    return ""


def interface(device, account, accept_language) -> str:
    """打开对话页时用哪种：这台设备记住的 → 账号语言 → 系统（浏览器）语言 → 英文。"""
    return normalize(device) or normalize(account) or from_accept_language(accept_language) or FALLBACK
