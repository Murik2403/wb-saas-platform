"""The one-process report worker must produce exactly what one process per metric produced.

Everything runs in fresh interpreters against a seeded demo database, because `config` reads the data directory at
import time. The comparison is on the artefacts the PDF is built from (title, summary, PNG bytes of every metric), then
on the text and page count of the finished PDF.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[2]
END = date.today()
START = END - timedelta(days=29)


@pytest.fixture(scope="module")
def data_dir(tmp_path_factory):
    path = tmp_path_factory.mktemp("report-data")
    env = {**os.environ, "WB_SAAS_DATA_DIR": str(path), "WB_SAAS_IS_DEMO": "1", "WB_SAAS_TENANT_SLUG": "demo", "MPLBACKEND": "Agg"}
    subprocess.run([sys.executable, "-c", "import seed_demo_data; seed_demo_data.main()"], cwd=APP, env=env, check=True, capture_output=True, timeout=300)
    return path


def env_for(data_dir, **extra):
    return {**os.environ, "WB_SAAS_DATA_DIR": str(data_dir), "MPLBACKEND": "Agg", **extra}


def codes(data_dir):
    out = subprocess.run([sys.executable, "-c", "import json; from reports.metrics import METRIC_BUILDERS; print(json.dumps(list(METRIC_BUILDERS)))"],
                         cwd=APP, check=True, capture_output=True, text=True, env=env_for(data_dir), timeout=120)
    return json.loads(out.stdout.strip().splitlines()[-1])


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def per_metric(data_dir, tmp_path, tag):
    """The previous behaviour: a fresh `metric_worker` process for each metric."""
    result = {}
    for code in codes(data_dir):
        png, meta = tmp_path / f"{tag}-{code}.png", tmp_path / f"{tag}-{code}.json"
        subprocess.run([sys.executable, "-m", "reports.metric_worker", code, START.isoformat(), END.isoformat(), str(png), str(meta)],
                       cwd=APP, env=env_for(data_dir), check=True, capture_output=True, timeout=300)
        result[code] = (json.loads(meta.read_text(encoding="utf-8")), sha(png))
    return result


def batch(data_dir, tmp_path):
    out = tmp_path / "batch"
    out.mkdir()
    names = codes(data_dir)
    subprocess.run([sys.executable, "-m", "reports.report_worker", START.isoformat(), END.isoformat(), str(out), *names],
                   cwd=APP, env=env_for(data_dir), check=True, capture_output=True, timeout=600)
    done = json.loads((out / "done.json").read_text(encoding="utf-8"))
    return {code: (json.loads((out / f"{i}.json").read_text(encoding="utf-8")), sha(out / f"{i}.png")) for i, code in enumerate(names)}, done


def test_every_metric_is_identical_to_the_one_process_per_metric_output(data_dir, tmp_path):
    first = per_metric(data_dir, tmp_path, "a")
    assert first == per_metric(data_dir, tmp_path, "b"), "the baseline must itself be deterministic or the comparison means nothing"
    new, done = batch(data_dir, tmp_path)
    assert new.keys() == first.keys() and len(new) == 21
    differing = [code for code in first if new[code] != first[code]]
    assert not differing, f"metrics whose title, summary or image changed: {differing}"
    assert done["metrics"] == 21 and done["dashboard_builds"] == 1, "the dashboard must be computed once per report, not once per metric"
    assert done["peak_rss_kb"] > 0


def pdf_text(data_dir, tmp_path, isolated, name):
    script = (
        "import sys; from datetime import date; from pypdf import PdfReader\n"
        "from reports.pdf_builder import _render_report_pdf\nfrom reports.metrics import METRIC_BUILDERS\n"
        f"pdf = _render_report_pdf('Отчёт', list(METRIC_BUILDERS), date.fromisoformat('{START}'), date.fromisoformat('{END}'))\n"
        "open(sys.argv[1], 'wb').write(pdf)\n"
    )
    target = tmp_path / name
    subprocess.run([sys.executable, "-c", script, str(target)], cwd=APP, check=True, capture_output=True, timeout=600,
                   env=env_for(data_dir, WB_SAAS_REPORT_ISOLATED_METRICS="1" if isolated else "0"))
    from pypdf import PdfReader
    reader = PdfReader(str(target))
    text = "\n".join(page.extract_text() for page in reader.pages)
    return re.sub(r"Сформировано: [\d. :]+", "Сформировано: <time>", text), len(reader.pages)


def test_the_finished_pdf_has_the_same_text_and_pages_as_the_in_process_render(data_dir, tmp_path):
    isolated_text, isolated_pages = pdf_text(data_dir, tmp_path, True, "isolated.pdf")
    reference_text, reference_pages = pdf_text(data_dir, tmp_path, False, "reference.pdf")
    assert isolated_pages == reference_pages
    assert isolated_text == reference_text


def test_a_failing_metric_names_itself_and_fails_the_report(data_dir, tmp_path):
    out = tmp_path / "bad"
    out.mkdir()
    done = subprocess.run([sys.executable, "-m", "reports.report_worker", START.isoformat(), END.isoformat(), str(out), "sales_orders", "no_such_metric"],
                          cwd=APP, env=env_for(data_dir), capture_output=True, text=True, timeout=300)
    assert done.returncode == 3 and "no_such_metric" in done.stderr
    assert not (out / "done.json").exists()
