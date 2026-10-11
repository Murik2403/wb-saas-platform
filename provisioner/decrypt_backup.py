"""Operator-only decrypt helper; extraction into a volume remains explicit."""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from backup_crypto import decrypt_file, decryption_options_from_environment


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    decrypt_file(args.archive, args.output, os.environ.get("WB_SAAS_BACKUP_ENCRYPTION_KEY", ""),
                 **decryption_options_from_environment())
    print(f"Decrypted {args.archive.name} to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
