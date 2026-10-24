import subprocess

import pytest

from quant.services.live_execution import ConfirmationTimeoutError, confirm_with_gui


def test_confirm_with_gui_times_out_closed(monkeypatch):
    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=kwargs.get("args") or args[0], timeout=kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(ConfirmationTimeoutError):
        confirm_with_gui("confirm?", timeout_seconds=1)
