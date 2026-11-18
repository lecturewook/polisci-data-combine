"""Copy shipped into each study. Run from the hub checkout or pass --hub."""

import argparse
import subprocess
import sys
from pathlib import Path

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--hub", type=Path, default=Path.cwd())
    p.add_argument(
        "--spec", type=Path, default=Path(__file__).resolve().parents[1] / "study.json"
    )
    p.add_argument("--engine", choices=["r", "stata"])
    args = p.parse_args()
    cmd = [
        sys.executable,
        "-m",
        "polihub",
        "pipeline",
        "--spec",
        str(args.spec.resolve()),
    ]
    if args.engine:
        cmd.extend(["--engine", args.engine])
    sys.exit(subprocess.run(cmd, cwd=args.hub, check=False).returncode)
