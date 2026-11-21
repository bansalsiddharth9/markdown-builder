"""specweb.yaml (design: Configuration, Security, Retention).

Paths come from the file or from environment variables (SPECWEB_DATA,
SPECWEB_CONFIG). Loading is strict like the other tools: unknown keys and
wrong types are errors.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

import yaml


class ConfigError(ValueError):
    """specweb.yaml is missing, malformed or has invalid values."""


@dataclass(frozen=True)
class WebConfig:
    """All specweb settings, with their defaults. Frozen: never changed at run time.

    Every field is a key allowed in specweb.yaml.
    """

    data_dir: Path = Path("data")
    # passed to the CLIs as --config; None: their own defaults
    specconv_config: Path | None = None
    specval_config: Path | None = None
    acceptance: Path | None = None
    step_timeout_s: float = 900.0  # per document and step (design: 15 minutes)
    worker_concurrency: int = 1
    max_file_mb: float = 100.0  # per uploaded file
    max_files: int = 20  # per job
    max_jobs_per_ip: int = 5  # queued or running jobs per client address
    max_queue: int = 50  # queued or running jobs in total
    retention_days: int = 30  # jobs older than this are deleted daily
    min_free_disk_mb: float = 1024.0  # new jobs are refused below this
    fetch_allowlist: tuple[str, ...] = field(default_factory=tuple)  # internal hosts allowed

    def to_dict(self) -> dict[str, Any]:
        """Plain values (paths as strings), so the config can go through the
        queue to the worker; from_dict reads it back."""
        return {k: (str(v) if isinstance(v, Path) else v) for k, v in asdict(self).items()}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WebConfig:
        """Build a config from a mapping, strictly: unknown keys, wrong types
        and negative numbers raise ConfigError.

        Only max_jobs_per_ip and min_free_disk_mb may be 0.
        """
        known = {f.name: f for f in fields(cls)}
        unknown = set(data) - set(known)
        if unknown:
            raise ConfigError(f"unknown keys {sorted(unknown)}; allowed: {sorted(known)}")
        values: dict[str, Any] = {}
        for name, value in data.items():
            default = getattr(cls(), name)
            if name in ("data_dir", "specconv_config", "specval_config", "acceptance"):
                values[name] = Path(value) if value is not None else None
            elif name == "fetch_allowlist":
                if not isinstance(value, list | tuple) or not all(
                    isinstance(v, str) for v in value
                ):
                    raise ConfigError("fetch_allowlist: expected a list of host names")
                values[name] = tuple(value)
            elif isinstance(default, bool) or not isinstance(value, int | float):
                raise ConfigError(f"{name}: expected a number, got {value!r}")
            elif value < 0 or (value == 0 and name not in ("max_jobs_per_ip", "min_free_disk_mb")):
                raise ConfigError(f"{name}: must be positive, got {value!r}")
            else:
                values[name] = type(default)(value)  # e.g. 100 -> 100.0 for a float field
        return cls(**values)


def load_config(path: Path | None = None) -> WebConfig:
    """Read specweb.yaml (path, else $SPECWEB_CONFIG, else defaults); $SPECWEB_DATA
    overrides data_dir. Relative paths in the file are relative to the file."""
    path = path or (
        Path(os.environ["SPECWEB_CONFIG"]) if os.environ.get("SPECWEB_CONFIG") else None
    )
    data: dict[str, Any] = {}
    if path is not None:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except FileNotFoundError as exc:
            raise ConfigError(f"{path}: file not found") from exc
        except yaml.YAMLError as exc:
            raise ConfigError(f"{path}: invalid YAML: {exc}") from exc
        if not isinstance(data, dict):
            raise ConfigError(f"{path}: expected a mapping")
        # So the file works the same from any working directory.
        for key in ("data_dir", "specconv_config", "specval_config", "acceptance"):
            if data.get(key) is not None:
                data[key] = str((path.parent / data[key]).resolve())
    if os.environ.get("SPECWEB_DATA"):
        data["data_dir"] = os.environ["SPECWEB_DATA"]
    try:
        return WebConfig.from_dict(data)
    except ConfigError as exc:
        raise ConfigError(f"{path or 'environment'}: {exc}") from exc
