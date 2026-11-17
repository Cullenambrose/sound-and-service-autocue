"""Refresh the current Python package and bundle its frozen backend into an Xcode app."""
import argparse
from pathlib import Path
import platform
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contents", type=Path, required=True,
                        help="Xcode-built .app/Contents directory")
    args = parser.parse_args()
    contents = args.contents.resolve()
    if contents.name != "Contents" or contents.parent.suffix != ".app" or contents.is_relative_to(ROOT):
        parser.error("--contents must be an Xcode-generated app bundle outside the source checkout")
    if sys.version_info < (3, 10) or platform.machine() not in ("arm64", "aarch64"):
        parser.error("Use native Apple Silicon Python 3.10 or newer for the app backend.")

    subprocess.run([
        sys.executable, "-m", "pip", "install", "--quiet", "--no-deps",
        "--no-build-isolation", "--force-reinstall", str(ROOT),
    ], check=True)
    sys.path.insert(0, str(ROOT))
    from scripts.build_macos_app import build_backend

    build_backend(contents, exclude_macos=True)


if __name__ == "__main__":
    main()
