from unittest.mock import Mock
import pandas as pd
from pandas.testing import assert_frame_equal
from reports import memory


def test_release_keeps_live_frames_and_metadata_intact():
    frame = pd.DataFrame({"value": [1.25, 2.5], "name": ["a", "b"]})
    expected = frame.copy(deep=True)
    memory.release_report_memory()
    assert_frame_equal(frame, expected, check_exact=True)


def test_unsupported_platform_uses_safe_fallback(monkeypatch):
    memory._system_trim.cache_clear()
    with monkeypatch.context() as context:
        context.setattr(memory.sys, "platform", "win32")
        loader = Mock(side_effect=AssertionError("native allocator must not load on Windows"))
        context.setattr(memory.ctypes, "CDLL", loader)
        memory.release_report_memory()
        loader.assert_not_called()
    memory._system_trim.cache_clear()
