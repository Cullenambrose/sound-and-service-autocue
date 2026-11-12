"""Dependency-only launcher preflight; imports no analyzers and installs nothing."""
import importlib.util
import sys


def check_runtime(*, version=None, find_spec=None):
    version = sys.version_info if version is None else version
    find_spec = importlib.util.find_spec if find_spec is None else find_spec
    if version < (3, 10):
        raise ValueError("Read-only Prep needs Python 3.10 or newer.")
    if find_spec("mutagen") is None:
        raise ValueError(
            "Missing mutagen. Install the project core dependencies in the repository virtual environment; "
            "no analyzer/model installation is needed. See docs/READ-ONLY-PREP-PILOT.md.")


def main():
    try:
        check_runtime()
    except ValueError as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    main()
