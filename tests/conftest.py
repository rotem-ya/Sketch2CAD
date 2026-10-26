"""Per-test deadline.

A real folder dialog or a converter waiting on a person must not hang the suite. When a test
exceeds SKETCH2CAD_TEST_TIMEOUT seconds (default 180), the process exits with a traceback.
Set the variable to 0 to disable the guard. No extra package: this is the stdlib faulthandler.
"""
from __future__ import annotations

import faulthandler
import os
import sys

import pytest

_TIMEOUT_S = float(os.environ.get("SKETCH2CAD_TEST_TIMEOUT", "180"))


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):  # noqa: ARG001 - pytest hook signature
    if _TIMEOUT_S > 0:
        faulthandler.dump_traceback_later(_TIMEOUT_S, exit=True, file=sys.__stderr__)
    try:
        yield
    finally:
        if _TIMEOUT_S > 0:
            faulthandler.cancel_dump_traceback_later()
