"""A scoped child must not load UI/chart runtimes just to serve JSON."""
import os
from pathlib import Path
import subprocess
import sys


def test_json_api_import_does_not_require_streamlit_or_chart_runtime(tmp_path):
    root = Path(__file__).resolve().parents[3]
    env = {key: value for key, value in os.environ.items()
           if key.upper() in {'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP'}}
    env.update(WB_SAAS_DATA_DIR=str(tmp_path), MH_LOCAL_JOBS='0',
               PYTHON_KEYRING_BACKEND='keyring.backends.null.Keyring')
    code = f'''
import importlib.abc,sys
sys.path[:0] = {[str(root / 'mh-redesign-lab/tenant-api'), str(root / 'tenant-app')]!r}
class NoDisplayRuntime(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in ('streamlit','matplotlib','plotly'):
            raise ImportError('JSON startup loaded a display runtime: '+fullname)
sys.meta_path.insert(0,NoDisplayRuntime())
import api_server
api_server._startup()
from ui_helpers import money,num,pct
from reports.catalog import METRIC_LABELS
assert money(125.5)=='126 ₽' and num(3)=='3' and pct(12.5)=='12.5%'
assert 'pnl' in METRIC_LABELS and len(METRIC_LABELS)==21
assert callable(api_server.build_report_pdf)
print('JSON_API_COLD_IMPORT_OK')
'''
    result = subprocess.run([sys.executable, '-c', code], env=env, cwd=tmp_path,
                            capture_output=True, text=True, timeout=40)
    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.strip() == 'JSON_API_COLD_IMPORT_OK'
