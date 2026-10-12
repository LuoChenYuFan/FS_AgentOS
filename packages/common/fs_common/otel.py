"""Minimal OpenTelemetry helpers. Spans follow run, node, agent, skill, and llm."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from opentelemetry import trace

_tracer = trace.get_tracer("fs_agentos")


def get_tracer() -> trace.Tracer:
    return _tracer


@contextmanager
def span(name: str, *, kind: str) -> Iterator[trace.Span]:
    with _tracer.start_as_current_span(name) as current:
        current.set_attribute("fs.kind", kind)
        yield current
