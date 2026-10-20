import importlib.util
from pathlib import Path
from unittest.mock import Mock

def _supervisor(monkeypatch, *, events: bool, consumer_exits: bool = False):
    path = Path(__file__).parents[1] / "scripts" / "start_urus_api_container.py"
    spec = importlib.util.spec_from_file_location("intraday_supervisor_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setenv("INTRADAY_EVENTS_ENABLED", str(events).lower())
    monkeypatch.setenv("INTRADAY_EQUITIES_ENABLED", "true")
    api = Mock()
    api.poll.return_value = None
    consumer = Mock()
    consumer.poll.return_value = 1 if consumer_exits else None
    start = Mock(side_effect=[api, consumer])
    terminate = Mock()
    handlers = {}
    monkeypatch.setattr(module, "_start", start)
    monkeypatch.setattr(module, "_terminate", terminate)
    monkeypatch.setattr(module, "_run_migrations", Mock())
    monkeypatch.setattr(module, "_wait_for_health", Mock())
    monkeypatch.setattr(module, "_uv_command", lambda: "uv")
    monkeypatch.setattr(module.signal, "signal", lambda sig, handler: handlers.update({sig: handler}))
    monkeypatch.setattr(module.time, "sleep", lambda _: handlers[module.signal.SIGTERM](None, None))
    return module, start, terminate, api, consumer


def test_default_supervisor_does_not_start_intraday_consumer(monkeypatch):
    module, start, terminate, api, _ = _supervisor(monkeypatch, events=False)
    assert module.main() == 0
    assert start.call_count == 1
    terminate.assert_any_call(api, 10.0)


def test_opt_in_consumer_starts_after_api_and_shuts_down(monkeypatch):
    module, start, terminate, api, consumer = _supervisor(monkeypatch, events=True)
    assert module.main() == 0
    assert start.call_count == 2
    assert start.call_args_list[1].args[0] == [
        "uv", "run", "python", "-m", "app.intraday.event_consumer", "--watch",
    ]
    terminate.assert_any_call(consumer, 10.0)
    terminate.assert_any_call(api, 10.0)


def test_consumer_crash_fails_supervisor_instead_of_silent_stale_events(monkeypatch):
    module, _, terminate, api, consumer = _supervisor(monkeypatch, events=True, consumer_exits=True)
    assert module.main() == 1
    terminate.assert_any_call(consumer, 10.0)
    terminate.assert_any_call(api, 10.0)
