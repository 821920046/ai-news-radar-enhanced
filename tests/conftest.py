from __future__ import annotations

import socket
import sys
from pathlib import Path
from urllib.parse import urlparse

import pytest
import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------------------
# 测试期禁止真实外网访问
# ---------------------------------------------------------------------------
# 起因（真实事故）：本地网络访问不到 translate.googleapis.com，于是
# `test_add_bilingual_fields_graceful_degradation_on_429` 在本地"通过"了 ——
# 因为 Google 请求超时失败、标题保持英文原样。但 GitHub Actions 的 runner
# **能**访问 Google，真实译文被写进 title_bilingual，断言随即在 CI 失败
# （run 36954738244：183 passed, 1 failed）。
#
# 教训：**测试通过与否依赖运行环境能不能联网，就是伪通过。**
#
# 实现要点（踩过两次坑，别退回）：
#   1. 只在 socket 层拦 **不够**。本机 requests 会从 Windows 注册表读到
#      `127.0.0.1:59173` 代理，socket 连的是 loopback（被放行），代理再替
#      我们出网 —— 请求照样成功。必须在 **URL 层** 拦，才知道真正要去哪。
#   2. 只清代理环境变量也不够（注册表回退），且会让诊断变得混乱。
#
# 因此以 URL 层拦截为主（覆盖 requests），socket 层拦截为辅（覆盖 httpx /
# urllib 等其它栈，仅拦非 loopback）。

_ALLOWED_HOSTS = {"127.0.0.1", "::1", "localhost", ""}


def _is_allowed(url_or_host: str) -> bool:
    if not url_or_host:
        return True
    if "://" in url_or_host:
        host = urlparse(url_or_host).hostname or ""
    else:
        host = url_or_host
    return host in _ALLOWED_HOSTS


@pytest.fixture(autouse=True)
def _block_external_network(monkeypatch, request):
    """拦截测试期的真实外网访问（loopback 除外）。

    如需在某个测试里放行外网，加 marker：``@pytest.mark.allow_network``。
    """
    if request.node.get_closest_marker("allow_network"):
        yield
        return

    # ── 主守卫：URL 层，覆盖 requests（含走代理的情况）──
    real_send = requests.Session.send

    def guarded_send(self, req, **kwargs):
        url = getattr(req, "url", "") or ""
        if not _is_allowed(url):
            raise RuntimeError(
                f"测试中检测到真实外网请求 -> {url!r}。"
                "请 mock 掉网络调用（例如 patch core.normalize.translator._google_session），"
                "或用 @pytest.mark.allow_network 显式放行。"
            )
        return real_send(self, req, **kwargs)

    monkeypatch.setattr(requests.Session, "send", guarded_send, raising=True)

    # ── 辅守卫：socket 层，覆盖非 requests 的 HTTP 栈 ──
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def _guard(address):
        if isinstance(address, tuple) and address:
            if not _is_allowed(str(address[0])):
                raise RuntimeError(
                    f"测试中检测到真实外网连接 -> {address!r}。"
                    "请 mock 掉网络调用，或用 @pytest.mark.allow_network 显式放行。"
                )

    def guarded_connect(self, address):
        _guard(address)
        return real_connect(self, address)

    def guarded_connect_ex(self, address):
        _guard(address)
        return real_connect_ex(self, address)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect, raising=True)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex, raising=True)
    yield


@pytest.fixture(autouse=True)
def _disable_index_translate_by_default(request, monkeypatch):
    """测试期默认关闭 Index-Translate 阶段，需显式打开才启用。

    它走**自己的** session 工厂（`_index_translate_session`），不会经过测试注入的
    mock session。不禁用会同时踩三个坑：
      1) 既有的 OpenRouter 路由池断言会静默变成 no-op —— 请求被 Index 阶段吃掉，
         mock 的 `session.post` 一次都没被调用，而断言仍然"通过"；
      2) 触发 `_block_external_network` 的 URL 层守卫，而那是 `RuntimeError`
         而非 `RequestException` —— 异常会穿透 `_index_translate_post` 的
         `except RequestException`，把整个 `add_bilingual_fields` 掀翻；
      3) 即使被拦住也是真实外网尝试，违反「测试通过不依赖能否联网」。
    """
    if request.node.get_closest_marker("index_translate"):
        yield  # 标记了就是要测它，由测试自行 patch 打开并 mock 网络
        return
    try:
        from core.normalize import translator as _t
    except Exception:  # pragma: no cover - 导入失败不该连带测试崩
        yield
        return
    if hasattr(_t, "INDEX_TRANSLATE_ENABLED"):
        monkeypatch.setattr(_t, "INDEX_TRANSLATE_ENABLED", False, raising=False)
    yield


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "index_translate: 该测试显式测试 Index-Translate 接入（自行 mock 网络）"
    )
    config.addinivalue_line(
        "markers", "allow_network: 允许该测试发起真实外网访问"
    )
