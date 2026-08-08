from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

_current_tracker: ContextVar["UsageTracker | None"] = ContextVar("usage_tracker", default=None)

PRICES = {
    "deepseek-v4-flash": {"input_cache_hit": 0.02, "input_cache_miss": 1.0, "output": 2.0},
    "deepseek-v4-pro": {"input_cache_hit": 0.025, "input_cache_miss": 3.0, "output": 6.0},
}

@dataclass
class UsageTracker:
    records: list[dict[str, Any]] = field(default_factory=list)

    def record(self, model: str, usage: Any) -> None:
        if not usage:
            return
        prompt = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion = int(getattr(usage, "completion_tokens", 0) or 0)
        cached = int(getattr(usage, "prompt_cache_hit_tokens", 0) or 0)
        prices = next((value for key, value in PRICES.items() if key in model.lower()), None)
        if not prices:
            self.records.append({"model": model, "prompt_tokens": prompt, "completion_tokens": completion, "cached_tokens": cached, "cost_cny": None})
            return
        uncached = max(0, prompt - cached)
        cost = (cached * prices["input_cache_hit"] + uncached * prices["input_cache_miss"] + completion * prices["output"]) / 1_000_000
        self.records.append({"model": model, "prompt_tokens": prompt, "completion_tokens": completion, "cached_tokens": cached, "cost_cny": round(cost, 8)})


def start_tracking() -> tuple[UsageTracker, object]:
    tracker = UsageTracker()
    return tracker, _current_tracker.set(tracker)


def stop_tracking(token: object) -> None:
    _current_tracker.reset(token)


def record_usage(model: str, usage: Any) -> None:
    tracker = _current_tracker.get()
    if tracker:
        tracker.record(model, usage)