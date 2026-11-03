"""Read local Gemini configuration without executing shell files or exposing credentials."""
import os
import re
import shlex
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODEL = "gemini-3.1-pro-preview"
NAMES = {"GEMINI_API_KEY", "GEM_MODEL"}


def configuration():
    values, sources = {}, {}
    explicit = os.environ.get("AUTOCUE_ENV_FILE")
    paths = [Path(explicit).expanduser()] if explicit else [ROOT / ".env.local", ROOT / ".env"]
    for path in paths:
        if not path.is_file():
            if explicit:
                raise ValueError("AUTOCUE_ENV_FILE does not point to a readable configuration file.")
            continue
        for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            name, separator, raw = line.strip().removeprefix("export ").partition("=")
            name = name.strip()
            if name not in NAMES or not separator or name in values:
                continue
            try:
                parts = shlex.split(raw, comments=True)
            except ValueError as error:
                raise ValueError(f"Invalid Gemini configuration syntax on line {index}.") from error
            if len(parts) > 1:
                raise ValueError(f"Quote Gemini configuration values containing spaces (line {index}).")
            if parts and parts[0]:
                values[name], sources[name] = parts[0], "AUTOCUE_ENV_FILE" if explicit else path.name
    for name in NAMES:
        if os.environ.get(name, "").strip():
            values[name], sources[name] = os.environ[name].strip(), "environment"
    model = values.get("GEM_MODEL", DEFAULT_MODEL).removeprefix("models/")
    if not re.fullmatch(r"[a-zA-Z0-9._-]+", model):
        raise ValueError("GEM_MODEL must be a model ID, not a URL or path.")
    return {"key": values.get("GEMINI_API_KEY"), "key_source": sources.get("GEMINI_API_KEY"),
            "model": model, "model_source": sources.get("GEM_MODEL", "default")}


def status():
    try:
        config = configuration()
    except (OSError, ValueError):
        return {"configured": False, "error": "Could not read Gemini configuration; check file permissions and syntax.",
                "access_verified": False}
    return {"configured": bool(config["key"]), "key_source": config["key_source"],
            "model": config["model"], "model_source": config["model_source"], "access_verified": False}
