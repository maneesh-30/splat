import sys

from splat.db import DatabaseUnavailable, health_check


def main() -> None:
    try:
        print(health_check())
    except DatabaseUnavailable as exc:
        print(exc, file=sys.stderr)
        sys.exit(1)
