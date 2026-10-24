import json
from pathlib import Path
from subprocess import CompletedProcess

from quant.services.live_execution import load_env_file
from quant.services import paper_signal_service
from scripts import restart_paper_signal_service as restart
from scripts.binance_usdm_monthly_report import load_env_file as report_load_env


def test_missing_env_file_allows_environment_only_configuration(tmp_path):
    assert load_env_file(tmp_path / "missing.env") == {}
    assert report_load_env(tmp_path / "missing.env") == {}


def test_default_intent_never_dispatches_live_execution(tmp_path, monkeypatch):
    config = json.loads(Path("config/paper-signal-service.example.json").read_text())
    from quant.services import live_execution

    def unexpected_execution(**kwargs):
        raise AssertionError("paper default attempted a live execution")

    monkeypatch.setattr(live_execution, "execute_intent", unexpected_execution)
    assert config["execution"]["mode"] == "intent_only"
    assert config["risk"]["max_live_positions"] == 1
    assert paper_signal_service.maybe_execute_live_intent(config, {}, tmp_path / "events.jsonl") is None


def test_restart_matches_only_absolute_service_path_in_this_checkout(monkeypatch):
    output = "\n".join([
        "100 1 100 python3 quant/services/paper_signal_service.py --confirm-live",
        "101 1 101 python3 /another-project/quant/services/paper_signal_service.py --confirm-live",
        f"102 1 102 python3 {restart.SERVICE_MARKER} --confirm-live",
    ])
    monkeypatch.setattr(restart, "run", lambda args: CompletedProcess(args, 0, output, ""))
    assert [row["pid"] for row in restart.matching_processes()] == [102]
