"""Reviewed public synthetic material; preview is presentation data, never a Run."""
import base64
import hashlib
import json
from pathlib import Path

from contextox.models import DemoCaseV1, DemoSourceFile


def demo_case() -> DemoCaseV1:
    directory = Path(__file__).resolve().parent / "demo"
    files = []
    for name, media in (("orders.csv", "text/csv"), ("customers.csv", "text/csv"), ("notes.md", "text/markdown")):
        content = (directory / name).read_bytes()
        files.append(DemoSourceFile(original_name=name, media_type=media,
            content_base64=base64.b64encode(content).decode("ascii"),
            sha256=hashlib.sha256(content).hexdigest()))
    preview = json.loads((directory / "preview.json").read_text(encoding="utf-8"))
    return DemoCaseV1(files=files, **preview)
