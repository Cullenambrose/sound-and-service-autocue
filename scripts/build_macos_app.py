"""Build an unsigned local-development app and DMG using an isolated build Python."""
import argparse
from datetime import datetime, timezone
import hashlib
from importlib.metadata import PackageNotFoundError, distribution
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import uuid

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

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


def _spdx_dependency_report(resources, app_distribution, build_tool_names):
    dependencies = {}
    edges = set()
    queue = [app_distribution]
    for name in build_tool_names:
        queue.append(distribution(name))

    while queue:
        installed = queue.pop()
        name = canonicalize_name(installed.metadata.get("Name") or installed.name)
        if name in dependencies:
            continue
        dependencies[name] = installed
        for raw_requirement in installed.requires or ():
            requirement = Requirement(raw_requirement)
            if requirement.marker and not requirement.marker.evaluate():
                continue
            dependency_name = canonicalize_name(requirement.name)
            edges.add((name, dependency_name))
            if dependency_name not in dependencies:
                try:
                    queue.append(distribution(dependency_name))
                except PackageNotFoundError as error:
                    raise RuntimeError(
                        f"Missing installed dependency {dependency_name} required by {name}."
                    ) from error

    def package_id(name):
        return "SPDXRef-Package-" + re.sub(r"[^A-Za-z0-9.-]", "-", name)

    packages = [{
        "name": "AutoCue Studio",
        "SPDXID": "SPDXRef-AutoCueStudio",
        "versionInfo": app_distribution.version,
        "downloadLocation": "NOASSERTION",
        "filesAnalyzed": False,
        "licenseConcluded": "NOASSERTION",
        "licenseDeclared": "NOASSERTION",
        "copyrightText": "NOASSERTION",
        "primaryPackagePurpose": "APPLICATION",
    }]
    for name, installed in sorted(dependencies.items()):
        package = {
            "name": installed.metadata.get("Name") or installed.name,
            "SPDXID": package_id(name),
            "versionInfo": installed.version,
            "downloadLocation": "NOASSERTION",
            "filesAnalyzed": False,
            "licenseConcluded": "NOASSERTION",
            "licenseDeclared": installed.metadata.get("License-Expression") or "NOASSERTION",
            "licenseInfoInFiles": ["NOASSERTION"],
            "copyrightText": "NOASSERTION",
            "primaryPackagePurpose": "LIBRARY",
            "externalRefs": [{
                "referenceCategory": "PACKAGE-MANAGER",
                "referenceType": "purl",
                "referenceLocator": f"pkg:pypi/{name}@{installed.version}",
            }],
        }
        packages.append(package)

    relationships = [{
        "spdxElementId": "SPDXRef-DOCUMENT",
        "relationshipType": "DESCRIBES",
        "relatedSpdxElement": "SPDXRef-AutoCueStudio",
    }, {
        "spdxElementId": "SPDXRef-AutoCueStudio",
        "relationshipType": "DEPENDS_ON",
        "relatedSpdxElement": "SPDXRef-CPython",
    }]
    for name, dependency_name in sorted(edges):
        if name == canonicalize_name(app_distribution.metadata.get("Name") or app_distribution.name):
            relationships.append({
                "spdxElementId": "SPDXRef-AutoCueStudio",
                "relationshipType": "DEPENDS_ON",
                "relatedSpdxElement": package_id(dependency_name),
            })
        else:
            relationships.append({
                "spdxElementId": package_id(name),
                "relationshipType": "DEPENDS_ON",
                "relatedSpdxElement": package_id(dependency_name),
            })
    for name in build_tool_names:
        relationships.append({
            "spdxElementId": package_id(canonicalize_name(name)),
            "relationshipType": "BUILD_TOOL_OF",
            "relatedSpdxElement": "SPDXRef-AutoCueStudio",
        })

    runtime = {
        "name": "CPython",
        "SPDXID": "SPDXRef-CPython",
        "versionInfo": sys.version.split()[0],
        "downloadLocation": "NOASSERTION",
        "filesAnalyzed": False,
        "licenseConcluded": "NOASSERTION",
        "licenseDeclared": "PSF-2.0",
        "licenseInfoInFiles": ["NOASSERTION"],
        "copyrightText": "NOASSERTION",
        "primaryPackagePurpose": "LIBRARY",
    }
    packages.append(runtime)
    report = {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": "AutoCue Studio Python dependency metadata closure",
        "documentNamespace": f"https://spdx.org/spdxdocs/AutoCueStudio-{uuid.uuid4()}",
        "creationInfo": {
            "creators": ["Tool: AutoCue Studio release inventory"],
            "created": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        },
        "packages": packages,
        "relationships": relationships,
        "comment": (
            "This is a metadata-derived Python dependency closure, including build-tool dependencies. "
            "It may include packages not embedded in the app and does not enumerate native components "
            "inside wheels; it is not a complete shipped-binary SBOM or a legal clearance."
        ),
    }
    path = resources / "AutoCueStudio.spdx.json"
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return {"path": path.name, "sha256": _sha256(path), "scope": report["comment"]}


def release_inventory(contents, *, exclude_macos=False):
    resources = contents / "Resources"
    notices = resources / "ThirdPartyNotices"
    notices.mkdir()
    app_distribution = distribution("sound-service-autocue")
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
                and (not exclude_macos or not path.is_relative_to(contents / "MacOS"))
                and (path.suffix in (".so", ".dylib") or path.name in ("AutoCueStudio", "studio-backend", "Python"))]
    sbom = _spdx_dependency_report(resources, app_distribution, ("pyinstaller",))
    report = {
        "schema_version": 1, "commercial_rights_cleared": False,
        "components": components, "native_binaries": binaries, "bundled_models": [], "sbom": sbom,
        "scope": "Installed declared dependencies, runtime/bootloader notices, exact native-file hashes "
                 "and a Python metadata-derived SPDX dependency closure. The SPDX closure may be broader "
                 "than the app's embedded contents and is not a complete shipped-binary SBOM or a legal clearance."
                 + (" Native files under Contents/MacOS are excluded because Xcode signs them after this "
                    "inventory is generated." if exclude_macos else ""),
        "release_gates": [
            "Mutagen declares GPL-2.0-or-later. Decide and satisfy applicable source/distribution obligations "
            "or replace it with a permitted alternative before commercial publication.",
            "Verify the PyInstaller bootloader exception, Python and scientific/native vendor notices "
            "against the exact distributed build.",
            "Review all transitive code/content rights and generate a complete shipped-binary SBOM; "
            "no model weights or optional analysis stacks are authorized by this inventory.",
        ],
    }
    (resources / "release-inventory.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def build_backend(contents, *, exclude_macos=False):
    """Freeze the installed Studio package and its web assets into an app bundle."""
    if sys.platform != "darwin" or platform.machine() not in ("arm64", "aarch64"):
        raise RuntimeError("The bundled Studio backend must be built with native Apple Silicon Python.")
    resources = contents / "Resources"
    resources.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "desktop/Branding/BoothBuddy.icns", resources / "BoothBuddy.icns")
    installed = distribution("sound-service-autocue")
    web = Path(installed.locate_file("web")).resolve()
    config = Path(installed.locate_file("autocue_config/genre_params_v6.json")).resolve()
    backend = resources / "backend"
    if backend.exists():
        shutil.rmtree(backend)
    notices = resources / "ThirdPartyNotices"
    if notices.exists():
        shutil.rmtree(notices)
    for generated in ("release-inventory.json", "AutoCueStudio.spdx.json"):
        path = resources / generated
        if path.exists():
            path.unlink()

    with tempfile.TemporaryDirectory(prefix="autocue-pyinstaller-", dir=contents.parent) as temporary:
        work_root = Path(temporary)
        dist = work_root / "dist"
        work = work_root / "work"
        command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                   "--onedir", "--name", "studio-backend", "--distpath", str(dist),
                   "--workpath", str(work), "--specpath", str(work),
                   "--hidden-import", "web.fixtures", "--hidden-import", "web.studio_cues",
                   *[part for name in ("studio.html", "studio.css", "studio.js",
                                       "studio-plan-view.js", "catalog-state.js")
                     for part in ("--add-data", f"{web / name}:web")],
                   "--add-data", f"{config}:autocue_config",
                   str(ROOT / "desktop/backend.py")]
        environment = dict(os.environ, PYINSTALLER_CONFIG_DIR=str(work_root / "cache"))
        result = subprocess.run(command, cwd=work_root, env=environment, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True)
        if result.returncode:
            print(result.stdout, file=sys.stderr)
            print("PyInstaller failed; captured diagnostics are shown above.", file=sys.stderr)
            raise SystemExit(result.returncode)
        shutil.move(str(dist / "studio-backend"), str(backend))
    release_inventory(contents, exclude_macos=exclude_macos)


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
    run(["/usr/bin/swiftc", "-parse-as-library", "-O", "-target", "arm64-apple-macosx12.0",
         str(ROOT / "desktop/StudioApp.swift"), "-o", str(contents / "MacOS/AutoCueStudio")])
    build_backend(contents)
    run(["/usr/bin/hdiutil", "create", "-quiet", "-format", "UDZO", "-volname",
         "AutoCue Studio", "-srcfolder", str(app), str(dmg)])
    print("Built unsigned development app and DMG.")


if __name__ == "__main__":
    main()
