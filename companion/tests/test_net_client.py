"""统一联网客户端（net.py）：没设 transport 工厂时与直接 httpx.Client(**kwargs) 完全相同；设了才注入，调用方给的优先。"""
import httpx
import pytest

from companion import http_tls, net


@pytest.fixture
def made(monkeypatch):
    seen = []

    def fake(*args, **kwargs):
        seen.append((args, kwargs))
        return object()

    monkeypatch.setattr(httpx, "Client", fake)
    yield seen
    net.set_transport_factory(None)


def _kwargs():
    return {
        "base_url": "https://a.pearnly.com",
        "headers": {"Authorization": "Bearer exp_x"},
        "timeout": httpx.Timeout(30.0, connect=10.0),
        "verify": http_tls.context(),
        "follow_redirects": False,
        "limits": httpx.Limits(max_connections=3),
    }


def test_default_passes_every_keyword_through_unchanged(made):
    given = _kwargs()
    net.client(**given)
    assert made == [((), given)]
    args, kwargs = made[0]
    assert all(kwargs[key] is given[key] for key in given)   # 同一个对象，不复制、不改写


def test_default_never_adds_a_transport_or_any_default(made):
    net.client()
    net.client(timeout=3.0)
    assert made == [((), {}), ((), {"timeout": 3.0})]


def test_factory_injects_a_transport_only_when_set(made):
    transport = httpx.MockTransport(lambda request: httpx.Response(200))
    asked = []
    net.set_transport_factory(lambda kwargs: asked.append(kwargs) or transport)
    given = _kwargs()
    net.client(**given)
    assert asked == [given]
    assert made[0][1] == {**given, "transport": transport}


def test_an_explicit_transport_wins_over_the_factory(made):
    mine = httpx.MockTransport(lambda request: httpx.Response(204))
    net.set_transport_factory(lambda kwargs: pytest.fail("factory must not be asked"))
    net.client(transport=mine, timeout=3.0)
    assert made == [((), {"transport": mine, "timeout": 3.0})]


def test_clearing_the_factory_restores_the_default(made):
    net.set_transport_factory(lambda kwargs: httpx.MockTransport(lambda request: httpx.Response(200)))
    net.client(timeout=1.0)
    net.set_transport_factory(None)
    net.client(timeout=2.0)
    assert "transport" in made[0][1]
    assert made[1] == ((), {"timeout": 2.0})


def test_a_real_client_goes_through_the_factory_transport():
    net.set_transport_factory(lambda kwargs: httpx.MockTransport(lambda request: httpx.Response(200, text="tunnel")))
    try:
        with net.client(base_url="https://a.pearnly.com", timeout=3.0) as client:
            assert client.get("/x").text == "tunnel"
    finally:
        net.set_transport_factory(None)
    with net.client(timeout=3.0) as client:
        assert isinstance(client, httpx.Client)
