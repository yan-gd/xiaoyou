import importlib.util
import sys
import threading
import time
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class _Logger:
    def __getattr__(self, _name):
        return lambda *args, **kwargs: None


common_module = types.ModuleType("common")
common_log_module = types.ModuleType("common.log")
common_log_module.logger = _Logger()
sys.modules.setdefault("common", common_module)
sys.modules.setdefault("common.log", common_log_module)

spec = importlib.util.spec_from_file_location(
    "xiaoyou_route_prefetch_test_module",
    ROOT / "plugins" / "xiaoyou_common" / "route_prefetch.py",
)
route_prefetch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(route_prefetch)
resolve_route_prefetch = route_prefetch.resolve_route_prefetch
start_route_prefetch = route_prefetch.start_route_prefetch


class _Context:
    def __init__(self):
        self.kwargs = {}


def test_routes_start_together_and_resolve_in_priority_order():
    context = _Context()
    ready = threading.Barrier(4)

    def provider(value, delay):
        ready.wait(timeout=1)
        time.sleep(delay)
        return value

    started = start_route_prefetch(
        context,
        {
            "REMINDERLOVE": lambda: provider(False, 0.04),
            "XIAOYOULIFEPHOTO": lambda: provider("photo", 0.09),
            "XIAOYOUMCP": lambda: provider(None, 0.06),
        },
    )
    assert started is True
    ready.wait(timeout=1)

    began = time.monotonic()
    assert (
        resolve_route_prefetch(
            context,
            "REMINDERLOVE",
            lambda: "fallback",
        )
        is False
    )
    assert (
        resolve_route_prefetch(
            context,
            "XIAOYOULIFEPHOTO",
            lambda: "fallback",
        )
        == "photo"
    )
    assert (
        resolve_route_prefetch(
            context,
            "XIAOYOUMCP",
            lambda: "fallback",
        )
        is None
    )
    elapsed = time.monotonic() - began

    assert elapsed < 0.14


def test_missing_prefetch_uses_inline_fallback():
    context = _Context()
    assert (
        resolve_route_prefetch(
            context,
            "REMINDERLOVE",
            lambda: "inline",
        )
        == "inline"
    )
