import os
import sys
import tempfile

import pytest

# ensure project src is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from airrelay.util.paths import resolve_paths  # noqa: E402


@pytest.fixture()
def home():
    """Function-scoped temp AIRRELAY_HOME for isolation."""
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture()
def paths(home):
    return resolve_paths(home)
