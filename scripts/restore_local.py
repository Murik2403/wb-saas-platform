"""Offline restore; refuses while supported application services are running."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tenant-app"))
from backup_tools import restore_backup

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--database-only", action="store_true")
    args = parser.parse_args()
    result = restore_backup(args.archive.read_bytes(), restore_settings=not args.database_only)
    print(result["message"])
    print("Safety backup:", result["safety_backup"])
