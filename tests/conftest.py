"""Publication tests never contact an exchange or external HTTP service."""

import pytest
import requests


@pytest.fixture(autouse=True)
def block_real_http(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Real HTTP requests are disabled in tests")

    monkeypatch.setattr(requests.sessions.Session, "request", blocked)
