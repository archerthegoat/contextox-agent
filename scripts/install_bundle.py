"""Install an already downloaded, checksum-verified bundle using its own Python."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile


def verify(bundle: Path) -> dict:
    bundle = bundle.resolve()
    metadata = json.loads((bundle / "BUILD.json").read_text())
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", metadata["version"]):
        raise ValueError("invalid release version")
    manifest = json.loads((bundle / "FILES.sha256.json").read_text())
    for relative, expected in manifest.items():
        path = bundle / relative
        if path.is_symlink() or bundle not in path.resolve().parents:
            raise ValueError("invalid manifest path")
        with path.open("rb") as handle:
            if hashlib.file_digest(handle, "sha256").hexdigest() != expected:
                raise ValueError("release file checksum mismatch")
    for path in bundle.rglob("*"):
        if path.is_symlink() and bundle not in path.resolve().parents:
            raise ValueError("release contains an external link")
        if path.is_file() and not path.is_symlink() and path.name != "FILES.sha256.json":
            if str(path.relative_to(bundle)) not in manifest:
                raise ValueError("release contains an unlisted file")
    return metadata


def install(bundle: Path, root: Path) -> Path:
    metadata = verify(bundle)
    if root.is_symlink():
        raise ValueError("install root must not be a symlink")
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    releases = root / "releases"
    if releases.is_symlink():
        raise ValueError("releases must not be a symlink")
    releases.mkdir(exist_ok=True)
    if not re.fullmatch(r"[0-9a-f]{40}", metadata["commit"]) or not re.fullmatch(r"[0-9a-f]{64}", metadata["content_hash"]):
        raise ValueError("invalid build identity")
    destination = releases / (metadata["version"] + "-" + metadata["commit"][:12] + "-" + metadata["content_hash"][:12])
    descriptor = os.open(root / "install.lock", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        launcher = root / "contextox"
        current = root / "current"
        if destination.is_symlink() or launcher.is_symlink():
            raise ValueError("installed files must not be external links")
        if launcher.exists() and not launcher.is_file():
            raise ValueError("launcher is not a regular file")
        if current.exists() and not current.is_symlink():
            raise ValueError("current is not an owned version link")
        if destination.exists():
            existing = verify(destination)
            if existing != metadata:
                raise ValueError("installed version differs; refusing to overwrite it")
        else:
            with tempfile.TemporaryDirectory(prefix=".install-", dir=root) as temporary:
                candidate = Path(temporary) / "bundle"
                shutil.copytree(bundle, candidate, symlinks=True)
                verify(candidate)
                candidate.rename(destination)
        candidate_link = root / f".current-{os.getpid()}"
        try:
            candidate_link.symlink_to(destination.relative_to(root), target_is_directory=True)
            candidate_link.replace(current)
        finally:
            candidate_link.unlink(missing_ok=True)
        launcher.write_text('#!/bin/sh\nset -eu\nbase=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)\n'
                            'if [ "$#" -eq 0 ]; then set -- start; fi\n'
                            'if [ "$1" = start ]; then\n  shift\n'
                            '  exec "$base/current/bin/contextox" start --agent-profile demo-fast --open-browser "$@"\n'
                            'fi\nexec "$base/current/bin/contextox" "$@"\n')
        launcher.chmod(0o755)
    return launcher


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install-dir", type=Path,
                        default=Path.home() / ".local" / "share" / "contextox")
    args = parser.parse_args()
    bundle = Path(__file__).resolve().parent
    print(install(bundle, args.install_dir.absolute()))


if __name__ == "__main__":
    main()
