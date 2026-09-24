"""Bounded, development-only timing and SQLite statement profiling."""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator


@dataclass
class _Timing:
    count: int = 0
    total_ms: float = 0.0
    min_ms: float = float("inf")
    max_ms: float = 0.0
    last_ms: float = 0.0

    def add(self, elapsed_ms: float) -> None:
        self.count += 1
        self.total_ms += elapsed_ms
        self.min_ms = min(self.min_ms, elapsed_ms)
        self.max_ms = max(self.max_ms, elapsed_ms)
        self.last_ms = elapsed_ms


class PerformanceProfiler:
    """Collect aggregate timings and counts without retaining SQL or user text."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._local = threading.local()
        self._timings: dict[str, _Timing] = defaultdict(_Timing)
        self._statements: dict[str, int] = defaultdict(int)

    @contextmanager
    def measure(self, name: str) -> Iterator[None]:
        started = time.perf_counter()
        try:
            yield
        finally:
            self.record(name, (time.perf_counter() - started) * 1000)

    @contextmanager
    def operation(self, name: str) -> Iterator[None]:
        previous = getattr(self._local, "operation", None)
        self._local.operation = name
        try:
            with self.measure(name):
                yield
        finally:
            self._local.operation = previous

    def record(self, name: str, elapsed_ms: float) -> None:
        with self._lock:
            self._timings[name].add(elapsed_ms)

    def count_statement(self) -> None:
        name = getattr(self._local, "operation", None)
        if name is None:
            return
        with self._lock:
            self._statements[name] += 1

    def statement_count(self, name: str) -> int:
        with self._lock:
            return self._statements.get(name, 0)

    def report(self) -> str:
        with self._lock:
            timings = tuple(sorted(self._timings.items()))
            statements = dict(self._statements)
        if not timings:
            return "Performance profile: no measured operations"
        lines = ["Performance profile (aggregate timings; SQL text is not recorded)"]
        for name, timing in timings:
            average = timing.total_ms / timing.count
            sql_count = statements.get(name)
            suffix = f"; SQL statements={sql_count}" if sql_count is not None else ""
            lines.append(
                f"{name}: avg={average:.2f} ms, last={timing.last_ms:.2f} ms, "
                f"min={timing.min_ms:.2f} ms, max={timing.max_ms:.2f} ms, "
                f"n={timing.count}{suffix}"
            )
        return "\n".join(lines)
