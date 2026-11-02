"""Cooperative checks for costly local reads; no instance is released while a decoder is still active."""

from contextlib import contextmanager
from contextvars import ContextVar
from time import monotonic

_gate = ContextVar("casework_checkpoint", default=None)


@contextmanager
def guarded(callback):
    token = _gate.set([callback, 0.0] if callback else None)
    try:
        pulse()
        yield
    finally:
        _gate.reset(token)


def pulse():
    gate = _gate.get()
    if gate and monotonic() - gate[1] >= 1:
        gate[0]()
        gate[1] = monotonic()
