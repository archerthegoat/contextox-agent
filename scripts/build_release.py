"""Build a relocatable macOS arm64 archive from reviewed pins; never publish it."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
PYTHON_URL = ("https://github.com/astral-sh/python-build-standalone/releases/download/"
              "20260825/cpython-3.14.7%2B20260825-aarch64-apple-darwin-install_only_stripped.tar.gz")
PYTHON_SHA256 = "17ecb3d29c49765856370cbb47d948a24ec518be40f607362c1bbb3ebbc5c442"


def digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def run(*command: str, cwd: Path = ROOT) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--allow-dirty", action="store_true", help="Build an explicitly marked local development artifact.")
    args = parser.parse_args()
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        parser.error("the reviewed build target is macOS arm64")
    dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT))
    if dirty and not args.allow_dirty:
        parser.error("commit the release source first, or explicitly use --allow-dirty for local checks")
    output = args.output_dir.resolve()
    if output == ROOT or ROOT in output.parents:
        parser.error("release artifacts must be built outside the repository")
    output.mkdir(parents=True, exist_ok=True)
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text())
    version = metadata["project"]["version"]
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    name = f"contextox-{version}-macos-arm64"
    archive = output / f"{name}.tar.gz"
    if archive.exists():
        parser.error("output archive already exists; choose a fresh output directory")
    # UV builds the app, resolves only the frozen lock, and is not needed by users.
    with tempfile.TemporaryDirectory(prefix="contextox-release-build-") as temporary:
        scratch = Path(temporary)
        static = scratch / "static"
        run("npm", "--prefix", "web", "run", "build", "--", "--outDir", str(static))
        bundle = scratch / name
        bundle.mkdir()
        runtime_archive = scratch / "python.tar.gz"
        with urlopen(Request(PYTHON_URL, headers={"User-Agent": "ContextOx-release-builder"}), timeout=60) as response, runtime_archive.open("wb") as target:
            shutil.copyfileobj(response, target)
        if digest(runtime_archive) != PYTHON_SHA256:
            raise RuntimeError("managed Python archive checksum mismatch")
        with tarfile.open(runtime_archive, "r:gz") as source:
            source.extractall(bundle, filter="data")
        python = bundle / "python" / "bin" / "python3"
        actual = subprocess.check_output([str(python), "-I", "-B", "-c", "import platform;print(platform.python_version())"], text=True).strip()
        if actual != (ROOT / ".python-version").read_text().strip():
            raise RuntimeError("managed Python version does not match the reviewed project pin")
        staging = scratch / "project"
        staging.mkdir()
        for filename in ("pyproject.toml", "README.md", "LICENSE"):
            shutil.copy2(ROOT / filename, staging / filename)
        package = staging / "src" / "contextox"
        package.mkdir(parents=True)
        # Only application Python and reviewed, explicit public resource directories.
        for source in (ROOT / "src" / "contextox").glob("*.py"):
            shutil.copy2(source, package / source.name)
        shutil.copytree(static, package / "static")
        shutil.copy2(ROOT / "web" / "src" / "assets" / "icons" / "LICENSE", package / "static" / "ICON_LICENSE.txt")
        if (ROOT / "src" / "contextox" / "demo").is_dir():
            shutil.copytree(ROOT / "src" / "contextox" / "demo", package / "demo",
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        wheels = scratch / "wheels"
        run("uv", "build", "--wheel", "--out-dir", str(wheels), cwd=staging)
        requirements = scratch / "requirements.txt"
        run("uv", "export", "--frozen", "--no-dev", "--no-emit-project", "--no-editable",
            "--output-file", str(requirements))
        library = bundle / "lib"
        run("uv", "pip", "install", "--python", str(python), "--target", str(library),
            "--no-deps", "--require-hashes", "--only-binary", ":all:", "-r", str(requirements))
        wheel = next(wheels.glob("contextox-*.whl"))
        run("uv", "pip", "install", "--python", str(python), "--target", str(library), "--no-deps", str(wheel))
        for local_reference in library.glob("*.dist-info/direct_url.json"):
            local_reference.unlink()  # Exclude local build directory references, not package licenses.
        (bundle / "launch.py").write_text(
            'from pathlib import Path\nimport sys\nsys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))\n'
            'from contextox.cli import main\nif __name__ == "__main__":\n    raise SystemExit(main())\n')
        binary = bundle / "bin" / "contextox"
        binary.parent.mkdir()
        binary.write_text('#!/bin/sh\nset -eu\nbase=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)\n'
                          'exec "$base/python/bin/python3" -I -B "$base/launch.py" "$@"\n')
        binary.chmod(0o755)
        shutil.copy2(ROOT / "scripts" / "install_bundle.py", bundle / "install_bundle.py")
        shutil.copy2(ROOT / "LICENSE", bundle / "LICENSE")
        shutil.copy2(ROOT / "uv.lock", bundle / "uv.lock")
        content_hash = hashlib.sha256(json.dumps({str(p.relative_to(bundle)): digest(p)
            for p in sorted(bundle.rglob("*")) if p.is_file() and not p.is_symlink()}, sort_keys=True).encode()).hexdigest()
        (bundle / "BUILD.json").write_text(json.dumps({
            "version": version, "commit": head, "dirty": dirty, "platform": "macos-arm64",
            "content_hash": content_hash,
            "python": actual, "python_archive_url": PYTHON_URL, "python_archive_sha256": PYTHON_SHA256,
            "lock_sha256": digest(ROOT / "uv.lock"), "default_demo_profile": "demo-fast",
            "human_acceptance": "PENDING",
        }, indent=2) + "\n")
        # Metadata and source URLs identify every pinned redistributable component.
        (bundle / "THIRD_PARTY_NOTICES.txt").write_text(
            "Python distribution: python-build-standalone, " + PYTHON_URL + "\n"
            "Python notices: python/lib/python3.14/LICENSE.txt and bundled runtime documentation.\n"
            "Python dependencies and their licenses: lib/*.dist-info/. Exact versions and hashes: uv.lock.\n"
            "Frontend license notices: lib/contextox/static/.\n")
        manifest = {str(path.relative_to(bundle)): digest(path) for path in sorted(bundle.rglob("*"))
                    if path.is_file() and not path.is_symlink()}
        (bundle / "FILES.sha256.json").write_text(json.dumps(manifest, indent=2) + "\n")
        run(str(binary), "doctor", "--json")
        with tarfile.open(archive, "w:gz", format=tarfile.PAX_FORMAT) as target:
            target.add(bundle, arcname=name)
    (output / "SHA256SUMS").write_text(f"{digest(archive)}  {archive.name}\n")
    shutil.copy2(ROOT / "scripts" / "install.sh", output / "install.sh")
    print(json.dumps({"archive": str(archive), "sha256": digest(archive), "commit": head, "dirty": dirty}))


if __name__ == "__main__":
    main()
