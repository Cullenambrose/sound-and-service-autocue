"""Explicit local project paths; environment overrides project JSON."""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def project_path(key, environment, default=None):
    config_file = os.environ.get("AUTOCUE_PROJECT")
    values = {}
    base = ROOT
    if config_file:
        path = Path(config_file).expanduser().resolve(strict=True)
        base = path.parent
        with path.open(encoding="utf-8") as source:
            values = json.load(source)
        if not isinstance(values, dict):
            raise ValueError("AutoCue project configuration must be a JSON object.")
    value = os.environ.get(environment, values.get(key, default))
    if value is None:
        raise ValueError(f"Configure {key} in AUTOCUE_PROJECT or set {environment}.")
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise ValueError(f"Invalid project path: {key}")
    path = Path(value).expanduser()
    return (path if path.is_absolute() else base / path).resolve()


def research_root():
    return project_path("research_root", "AUTOCUE_RESEARCH_ROOT")
