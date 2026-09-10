"""Record the tested Windows environments without paths or credentials."""
from importlib.metadata import distributions
from pathlib import Path
import sys

if sys.platform != "win32":
    raise SystemExit("Run inside the tested Windows virtual environment")
target = Path(sys.argv[1]).resolve()
rows = sorted({f"{dist.metadata['Name']}=={dist.version}" for dist in distributions() if dist.metadata.get("Name")})
target.write_text("# Tested Windows environment; do not use as Linux constraints.\n" + "\n".join(rows) + "\n", encoding="utf-8")
