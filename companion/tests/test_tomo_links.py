"""Tomo 桌面窗口的地址规则（2026-10-10）：只在窗口里开 Pearnly 自己的 https 页面，回到上次的对话页，下载不覆盖。"""
from pathlib import Path

from src.companion import tomo_links as links


def test_only_pearnly_https_pages_open_in_the_window():
    assert links.route("https://fatcc.com/") == "load"
    assert links.route("https://www.fatcc.com/") == "load"
    assert links.route("https://fatcc.com.evil.io/") == "external"
    assert links.route("https://auth.pearnly.com/realms/x") == "load"
    assert links.route("https://acme.pearnly.com/assistant") == "load"
    assert links.route("https://pearnly.com/privacy/") == "load"
    assert links.route("http://acme.pearnly.com/assistant") == "external"
    assert links.route("https://evilpearnly.com/") == "external"
    assert links.route("https://pearnly.com.evil.io/") == "external"
    assert links.route("https://line.me/R/ti/p/@pearnly") == "external"
    assert links.route("mailto:hello@pearnly.com") == "external"
    assert links.route("file:///C:/Users/a/secret.xlsx") == "block"
    assert links.route("data:text/html,hi") == "block"
    assert links.route("javascript:alert(1)") == "block"


def test_resume_returns_to_the_last_chat_page_of_the_same_entry():
    prod, test = {}, {"PEARNLY_TOMO_ENTRY": "https://testai.pearnly.com/"}
    assert links.resume_url("", None, prod) == "https://fatcc.com"
    assert links.resume_url("https://acme.pearnly.com/assistant", "https://fatcc.com", prod) == \
        "https://acme.pearnly.com/assistant"
    assert links.resume_url("https://acme.pearnly.com/app/home", "https://fatcc.com", prod) == "https://fatcc.com"
    assert links.resume_url("https://example.com/assistant", "https://fatcc.com", prod) == "https://fatcc.com"
    assert links.resume_url("https://testcowork.pearnly.com/assistant", "https://testai.pearnly.com", test) == \
        "https://testcowork.pearnly.com/assistant"
    assert links.entry_url({"PEARNLY_TOMO_ENTRY": "https://evil.example"}) == "https://fatcc.com"


def test_test_tenants_reached_through_the_official_entry_come_back_to_their_last_page():
    # 2026-11-03：正式入口 fatcc.com 下 test 开头的租户（testerp 等）以前一律不回上次的对话页，每次从托盘打开都改走
    # fatcc.com 重新登录（1.4.0 起按域名前缀猜测试环境）；现在只看存下它时用的入口
    for site in ("https://testerp.pearnly.com/assistant", "https://testcowork.pearnly.com/assistant?c=s-123"):
        assert links.resume_url(site, "https://fatcc.com", {}) == site
        assert links.resume_url(site, "https://FATCC.com/", {}) == site      # 大小写、末尾 / 不算不同


def test_a_page_saved_under_another_entry_is_not_reused():
    test = {"PEARNLY_TOMO_ENTRY": "https://testai.pearnly.com"}
    assert links.resume_url("https://acme.pearnly.com/assistant", "https://fatcc.com", test) == "https://testai.pearnly.com"
    assert links.resume_url("https://testerp.pearnly.com/assistant", "https://testai.pearnly.com", {}) == "https://fatcc.com"


def test_old_registry_without_an_entry_record_goes_back_to_the_last_page():
    # 旧版只存 last_url：没有入口记录按「入口相同」处理，回上次的对话页，会话失效由站点送去 CC 入口
    # （pearnly_core/login_redirect.py）；不是对话页照旧从入口进
    for missing in (None, "", "  "):
        assert links.resume_url("https://testerp.pearnly.com/assistant", missing, {}) == \
            "https://testerp.pearnly.com/assistant"
        assert links.resume_url("https://acme.pearnly.com/assistant", missing, {}) == "https://acme.pearnly.com/assistant"
        assert links.resume_url("https://acme.pearnly.com/app/home", missing, {}) == "https://fatcc.com"
        assert links.resume_url("", missing, {}) == "https://fatcc.com"


def test_the_window_saves_the_entry_with_the_last_page_and_passes_it_back():
    source = (Path(links.__file__).with_name("gui_tomo.py")).read_text(encoding="utf-8")
    remember = source.split("def _remember(self, url: QUrl)", 1)[1].split("def closeEvent", 1)[0]
    assert 'self._settings.setValue("last_url", value)' in remember
    assert 'self._settings.setValue("last_entry", tomo_links.entry_url())' in remember
    assert 'tomo_links.resume_url(str(self._settings.value("last_url", "") or ""),\n' \
           '                                                    self._settings.value("last_entry", None))' in source
    assert "startswith(\"test\")" not in Path(links.__file__).read_text(encoding="utf-8")


def test_web_page_language_follows_the_windows_display_languages():
    # 2026-10-13：泰文 Windows 的新电脑，窗口里的登录页要按系统显示泰文，第一次登录后对话页也是泰文
    assert links.accept_language(["th-TH", "en-US"]) == "th-TH,th,en-US,en"
    assert links.accept_language(["zh-Hans-CN", "zh-CN", "en"]) == "zh-Hans-CN,zh,zh-CN,en"
    assert links.accept_language(["en_US", "EN-us"]) == "en-US,en"
    assert links.accept_language(["th-TH\r\nX-Evil: 1", "", None, "C"]) == ""
    assert links.accept_language([]) == ""
    source = (Path(links.__file__).with_name("gui_tomo.py")).read_text(encoding="utf-8")
    window = source.split("self.profile = QWebEngineProfile(", 1)[1].split("self.view = QWebEngineView(", 1)[0]
    assert "tomo_links.accept_language(QLocale.system().uiLanguages())" in window
    assert "self.profile.setHttpAcceptLanguage(accept)" in window


def test_quick_question_is_handed_to_the_page_as_a_string():
    script = links.ask_script('a"b</script>\n')
    assert 'window.tomo.ask("a\\"b</script>\\n")' in script


def test_reminders_open_their_conversation_on_the_company_site():
    # 2026-10：托盘的对话提醒点开那段对话，没有对话就是定时任务页（与共用层 contracts.deep_link 同一形状）
    assert links.conversation_url("https://acme.pearnly.com", "sess_1:a") == \
        "https://acme.pearnly.com/assistant?c=sess_1%3Aa"
    assert links.conversation_url("https://acme.pearnly.com/", "") == "https://acme.pearnly.com/assistant?view=tasks"
    assert links.conversation_url("https://acme.pearnly.com", "../x?y") == "https://acme.pearnly.com/assistant?view=tasks"
    assert links.conversation_url("https://evil.example", "sess-1") == ""
    assert links.conversation_url("http://acme.pearnly.com", "sess-1") == ""
    assert links.conversation_url("", "sess-1") == ""
    assert links.same_site("https://acme.pearnly.com/assistant", "https://acme.pearnly.com/assistant?c=s-123")
    assert not links.same_site("https://acme.pearnly.com/assistant", "https://other.pearnly.com/assistant")
    assert not links.same_site("", "")


def test_open_script_hands_the_target_to_the_page_or_falls_back_to_the_address():
    url = "https://acme.pearnly.com/assistant?c=s-123"
    script = links.open_script("s-123", url)
    assert 't.open("s-123")' in script
    assert 'location.href="https://acme.pearnly.com/assistant?c=s-123"' in script   # 旧版页面没有 open
    assert 't.open("")' in links.open_script('bad"id</script>', url)               # 不合形状 = 定时任务页
    # 1.9.1：公司主档页 / 待处理交页内深链接（frontend/assistant/src/lib/nav.ts 的 useDeepLinks 认「/assistant?…」）
    assert 't.open("/assistant?company=abc1234567")' in links.open_script("/assistant?company=abc1234567", url)
    assert 't.open("/assistant?view=pending")' in links.open_script("/assistant?view=pending", url)
    for bad in ("/assistant?company=../x", "/assistant?view=chat", "/assistant?c=s-1&company=x", "/other?view=pending",
                "/assistant?view=tasks"):
        assert 't.open("")' in links.open_script(bad, url), bad


def test_reminders_of_one_company_open_that_companys_page():
    # 1.9.1（旧交接 T3）：提醒的 view = company:<编号>（服务端 NOTICE_PROJECT_VIEW）且没挂对话 → 主档页 ?company=<编号>；
    # 有对话照旧开对话；pending 开待处理；认不得的照旧定时任务页（同共用层 contracts.deep_link）
    site = "https://acme.pearnly.com"
    assert links.conversation_url(site, "", "company:abc1234567") == f"{site}/assistant?company=abc1234567"
    assert links.conversation_url(site, "s-123", "company:abc1234567") == f"{site}/assistant?c=s-123"
    assert links.conversation_url(site, "", "pending") == f"{site}/assistant?view=pending"
    for bad in ("company:", "company:../x", "company:" + "a" * 13, "company:abc", "companies", "chat", None):
        assert links.conversation_url(site, "", bad) == f"{site}/assistant?view=tasks", bad
    assert links.open_target("", "company:abc1234567") == "/assistant?company=abc1234567"
    assert links.open_target("s-123", "company:abc1234567") == "s-123"
    assert links.open_target("", "") == links.open_target("", "tasks") == links.open_target("", "company:../x") == ""
    assert links.open_target("", "pending") == "/assistant?view=pending"


def test_downloads_never_overwrite_and_clean_names(tmp_path: Path):
    first = links.unique_path(tmp_path, "report.xlsx")
    assert first.name == "report.xlsx"
    first.write_text("x")
    assert links.unique_path(tmp_path, "report.xlsx").name == "report (1).xlsx"
    assert links.unique_path(tmp_path, 'a/b:c*?.xlsx').name == "a_b_c__.xlsx"
    assert links.unique_path(tmp_path, "").name == "download"


def test_new_windows_from_a_company_page_stay_signed_in_inside_cc():
    # 2026-11-04：ERP 公司页「在浏览器中打开」、ERP 待处理的单据、Cowork 定时任务「查看需要处理的」都是 window.open / target=_blank，
    # 以前一律交给系统浏览器，那边没有站点会话、落到登录页。同一站点的改在 CC 子窗口里开（同一份登录），别的照旧给系统浏览器。
    chat = "https://testerp.pearnly.com/assistant?c=s-1"
    assert links.popup_route("https://testerp.pearnly.com/desk/pearnly-company/CO-00001", chat) == "window"
    assert links.popup_route("https://testerp.pearnly.com/desk/pearnly-purchase/PUR-1", chat) == "window"
    assert links.popup_route("https://testcowork.pearnly.com/desk/pearnly-intake?push_state=failed",
                             "https://testcowork.pearnly.com/assistant") == "window"
    # 别的公司站点、入口、公开页：交给系统浏览器（不在 CC 里把另一家的登录带过去）
    assert links.popup_route("https://testcowork.pearnly.com/desk/x", chat) == "external"
    assert links.popup_route("https://pearnly.com/privacy/", chat) == "external"
    assert links.popup_route("https://fatcc.com/", chat) == "external"
    assert links.popup_route("https://line.me/R/ti/p/@pearnly", chat) == "external"
    # 同主机但不是 https、端口不同、本地文件：不当同一站点
    assert links.popup_route("http://testerp.pearnly.com/desk/x", chat) == "external"
    assert links.popup_route("https://testerp.pearnly.com:8443/desk/x", chat) == "external"
    assert links.popup_route("file:///C:/x.xlsx", chat) == "block"
    assert links.popup_route("data:text/html,hi", chat) == "block"
    # 打开它的页面不是 Pearnly 的（还在空白页）：一律不当同站
    assert links.popup_route("https://testerp.pearnly.com/desk/x", "") == "external"


def test_the_window_sends_same_site_new_windows_to_its_own_child_window():
    source = (Path(__file__).resolve().parents[1] / "src/companion/gui_tomo.py").read_text(encoding="utf-8")
    popup = source.split("class _Popup(QWebEnginePage):", 1)[1].split("\nclass ", 1)[0]
    assert "tomo_links.popup_route(url.toString(), self._opener.url().toString())" in popup
    assert '_SiteWindow(self.profile(), url' in popup        # 同一个网页配置 = 同一份登录
    assert "QDesktopServices.openUrl(url)" in popup
    child = source.split("class _SiteWindow(QMainWindow):", 1)[1].split("\nclass ", 1)[0]
    assert "TomoPage(profile, self.view)" in child          # 子窗口里的链接、再开的新窗口照主窗口的规则
    assert "WA_DeleteOnClose" in child and "self.view.setPage(None)" in child   # 关了就释放，不常驻
    assert "return _Popup(self.profile(), self)" in source
