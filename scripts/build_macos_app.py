"""Build an unsigned local-development app and DMG using an isolated build Python."""
import argparse
import hashlib
from importlib.metadata import distribution
import json
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig

ROOT = Path(__file__).resolve().parents[1]


def run(command, **kwargs):
    print("+", " ".join(Path(part).name if "/" in part else part for part in command), flush=True)
    subprocess.run(command, check=True, **kwargs)


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def release_inventory(contents):
    resources = contents / "Resources"
    notices = resources / "ThirdPartyNotices"
    notices.mkdir()
    components = []
    for name in ("numpy", "scipy", "mutagen", "pyinstaller"):
        installed = distribution(name)
        files = [Path(str(file)) for file in installed.files or ()
                 if Path(str(file)).name.lower().startswith(("license", "copying", "notice"))]
        if not files:
            raise RuntimeError(f"Missing installed license files for {name}; development package was not completed.")
        copied = []
        for file in sorted(files):
            if file.is_absolute() or ".." in file.parts:
                raise ValueError(f"Unsafe installed license path for {name}.")
            source = Path(installed.locate_file(file))
            destination = notices / name / file
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            copied.append({"path": str(destination.relative_to(resources)), "sha256": _sha256(destination)})
        components.append({
            "name": name, "version": installed.version,
            "role": "build-tool and shipped bootloader" if name == "pyinstaller" else "declared runtime dependency",
            "license_expression": installed.metadata.get("License-Expression"),
            "license_classifiers": [value for value in installed.metadata.get_all("Classifier", ())
                                    if value.startswith("License ::")],
            "notices": copied,
        })
    python_license = Path(sysconfig.get_path("stdlib")) / "LICENSE.txt"
    if not python_license.is_file():
        raise RuntimeError("Python runtime license is missing; development package was not completed.")
    destination = notices / "python" / "LICENSE.txt"
    destination.parent.mkdir()
    shutil.copy2(python_license, destination)
    components.append({"name": "CPython", "version": sys.version.split()[0], "role": "bundled runtime",
                       "license_expression": "PSF-2.0", "notices": [
                           {"path": str(destination.relative_to(resources)), "sha256": _sha256(destination)}]})
    binaries = [{"path": str(path.relative_to(contents)), "bytes": path.stat().st_size,
                 "sha256": _sha256(path)}
                for path in sorted(contents.rglob("*")) if path.is_file()
                and (path.suffix in (".so", ".dylib") or path.name in ("AutoCueStudio", "studio-backend", "Python"))]
    report = {
        "schema_version": 1, "commercial_rights_cleared": False,
        "components": components, "native_binaries": binaries, "bundled_models": [],
        "scope": "Installed declared dependencies, runtime/bootloader notices and exact native-file hashes. "
                 "Not a complete transitive SPDX SBOM or a legal clearance.",
        "release_gates": [
            "Mutagen declares GPL-2.0-or-later. Decide and satisfy applicable source/distribution obligations "
            "or replace it with a permitted alternative before commercial publication.",
            "Verify the PyInstaller bootloader exception, Python and scientific/native vendor notices "
            "against the exact distributed build.",
            "Review all transitive code/content rights and generate a complete release SBOM; "
            "no model weights or optional analysis stacks are authorized by this inventory.",
        ],
    }
    (resources / "release-inventory.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="Dedicated build output directory")
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("macOS and Xcode command line tools are required")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    app = output / "AutoCue Studio.app"
    dmg = output / "AutoCue Studio.dmg"
    work = output / "pyinstaller-work"
    dist = output / "pyinstaller-dist"
    if app.exists() or dmg.exists() or work.exists() or dist.exists():
        parser.error("Output already contains a build; choose an empty dedicated output directory")
    contents = app / "Contents"
    (contents / "MacOS").mkdir(parents=True)
    (contents / "Resources").mkdir()
    shutil.copy2(ROOT / "desktop/Info.plist", contents / "Info.plist")
    installed = distribution("sound-service-autocue")
    web = Path(installed.locate_file("web")).resolve()
    config = Path(installed.locate_file("autocue_config/genre_params_v6.json")).resolve()
    run(["/usr/bin/swiftc", "-O", "-target", "arm64-apple-macosx12.0",
         str(ROOT / "desktop/StudioApp.swift"), "-o", str(contents / "MacOS/AutoCueStudio")])
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
               "--onedir", "--name", "studio-backend", "--distpath", str(dist),
               "--workpath", str(work), "--specpath", str(work),
               "--hidden-import", "web.fixtures", "--hidden-import", "web.studio_cues",
               *[part for name in ("studio.html", "studio.css", "studio.js", "catalog-state.js")
                 for part in ("--add-data", f"{web / name}:web")],
               "--add-data", f"{config}:autocue_config",
               str(ROOT / "desktop/backend.py")]
    # Keep PyInstaller's verbose path-bearing diagnostics out of build artifacts.
    result = subprocess.run(command, cwd=output, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if result.returncode:
        print("PyInstaller failed; rerun the build command directly for diagnostics.", file=sys.stderr)
        raise SystemExit(result.returncode)
    shutil.move(str(dist / "studio-backend"), str(contents / "Resources/backend"))
    release_inventory(contents)
    shutil.rmtree(work)
    shutil.rmtree(dist)
    run(["/usr/bin/hdiutil", "create", "-quiet", "-format", "UDZO", "-volname",
         "AutoCue Studio", "-srcfolder", str(app), str(dmg)])
    print("Built unsigned development app and DMG.")


if __name__ == "__main__":
    main()
