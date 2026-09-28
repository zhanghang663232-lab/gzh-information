from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

from .proxy import MacProxyManager


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent", type=int, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--lease", type=Path, required=True)
    args = parser.parse_args()
    while args.lease.exists():
        try:
            os.kill(args.parent, 0)
        except OSError:
            MacProxyManager(args.state).restore()
            return
        time.sleep(1)


if __name__ == "__main__":
    main()


