#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.db.backup import plan_backup_retention


def main():
    parser = argparse.ArgumentParser(description="Produce an audit-only backup retention plan.")
    parser.add_argument("backup_root")
    parser.add_argument("--keep-minimum", type=int, default=1)
    parser.add_argument("--output")
    args = parser.parse_args()
    root = Path(args.backup_root)
    report = plan_backup_retention(
        sorted(root.glob("*.manifest.json")),
        keep_minimum=args.keep_minimum,
    )
    serialized = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(serialized, encoding="utf-8")
        output.chmod(0o600)
    print(serialized, end="")


if __name__ == "__main__":
    main()
