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


def test_resume_returns_to_the_last_chat_page_of_the_same_side():
    assert links.resume_url("", {}) == "https://fatcc.com"
    assert links.resume_url("https://acme.pearnly.com/assistant", {}) == "https://acme.pearnly.com/assistant"
    assert links.resume_url("https://acme.pearnly.com/app/home", {}) == "https://fatcc.com"
    assert links.resume_url("https://example.com/assistant", {}) == "https://fatcc.com"
    test = {"PEARNLY_TOMO_ENTRY": "https://testai.pearnly.com/"}
    assert links.resume_url("https://acme.pearnly.com/assistant", test) == "https://testai.pearnly.com"
    assert links.resume_url("https://testcowork.pearnly.com/assistant", test) == "https://testcowork.pearnly.com/assistant"
    assert links.resume_url("https://testcowork.pearnly.com/assistant", {}) == "https://fatcc.com"
    assert links.entry_url({"PEARNLY_TOMO_ENTRY": "https://evil.example"}) == "https://fatcc.com"


def test_quick_question_is_handed_to_the_page_as_a_string():
    script = links.ask_script('a"b</script>\n')
    assert 'window.tomo.ask("a\\"b</script>\\n")' in script


def test_downloads_never_overwrite_and_clean_names(tmp_path: Path):
    first = links.unique_path(tmp_path, "report.xlsx")
    assert first.name == "report.xlsx"
    first.write_text("x")
    assert links.unique_path(tmp_path, "report.xlsx").name == "report (1).xlsx"
    assert links.unique_path(tmp_path, 'a/b:c*?.xlsx').name == "a_b_c__.xlsx"
    assert links.unique_path(tmp_path, "").name == "download"
