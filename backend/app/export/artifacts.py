"""Publish complete artifacts in a version-isolated directory."""
from hashlib import sha256
from pathlib import Path
import re
from uuid import uuid4
from ..contracts.services import Artifact
from .pdf import export_error

def version_component(version: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", version):
        raise export_error("invalid_version")
    return version

def publish(file: Path, runtime: Path, version: str) -> Artifact:
    relative = Path("exports") / version_component(version) / uuid4().hex / file.name
    target = (runtime / relative).resolve()
    if not target.is_relative_to(runtime.resolve()):
        raise export_error("invalid_path")
    target.parent.mkdir(parents=True, exist_ok=False)
    try:
        payload = file.read_bytes()
        file.replace(target)
    except OSError:
        target.parent.rmdir()
        raise
    return Artifact(storage_key=relative.as_posix(), sha256=sha256(payload).hexdigest(), size_bytes=len(payload))
