"""Run database tooling directly: python -m db <command>."""
import sys
from db.cli import main

if __name__ == '__main__':
    sys.exit(main())
