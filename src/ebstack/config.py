from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

from .errors import EbstackError
from .models import (
    Architecture,
    Layer,
    StackConfig,
    VALID_CPU_VENDORS,
    VALID_JOB_KEYS,
    VALID_LAYERS,
    VALID_STACKS,
)

VALID_TOP_LEVEL = {"defaults", "architectures", "layers"}
VALID_LAYER_FIELDS = {"easyconfigs", "options"}
VALID_ARCH_FIELDS = {"cpu_vendor", "stack", "cuda_compute_capabilities", "jobs"}
VALID_PATH_KEYS = {"log_root"}
ENV_WITH_DEFAULT = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*):-([^}]*)\}")


def _fail(message: str) -> None:
    raise EbstackError(message)


def _mapping(value: object, path: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        _fail(f"{path} must be a mapping")
    return value


def _string_list(value: object, path: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        _fail(f"{path} must be a list")
    for item in value:
        if not isinstance(item, str):
            _fail(f"{path} entries must be strings")
    return tuple(value)


def _string_value(value: object, path: str) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, int):
        return str(value)
    _fail(f"{path} must be a string or integer")


def _expand_env(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        name, default = match.groups()
        return os.environ.get(name, default)

    return os.path.expandvars(ENV_WITH_DEFAULT.sub(replace, value))


def _path_value(value: object, path: str, *, config_dir: Path) -> Path:
    raw = _string_value(value, path)
    expanded = Path(_expand_env(raw)).expanduser()
    if not expanded.is_absolute():
        expanded = config_dir / expanded
    return expanded


def _jobs(value: object, path: str) -> dict[str, str]:
    jobs = _mapping(value, path)
    unknown_keys = set(jobs) - VALID_JOB_KEYS
    if unknown_keys:
        _fail(f"Unknown job key(s) in {path}: {', '.join(sorted(unknown_keys))}")
    return {key: _string_value(val, f"{path}.{key}") for key, val in jobs.items()}


def load_stack_config(path: Path) -> StackConfig:
    path = path.expanduser().resolve()
    try:
        with path.open("r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle)
    except OSError as err:
        _fail(f"Could not read {path}: {err}")
    except yaml.YAMLError as err:
        _fail(f"Could not parse {path}: {err}")

    data = _mapping(raw, str(path))
    unknown_top = set(data) - VALID_TOP_LEVEL
    if unknown_top:
        _fail(f"Unknown top-level section(s): {', '.join(sorted(unknown_top))}")

    defaults = _mapping(data.get("defaults"), "defaults")
    unknown_defaults = set(defaults) - {"jobs", "paths"}
    if unknown_defaults:
        _fail(f"Unknown defaults section(s): {', '.join(sorted(unknown_defaults))}")
    defaults_jobs = _jobs(defaults.get("jobs", {}), "defaults.jobs")
    log_root = _load_log_root(defaults, path)

    return StackConfig(
        path=path,
        log_root=log_root,
        defaults_jobs=defaults_jobs,
        architectures=_load_architectures(data),
        layers=_load_layers(data),
    )


def _load_log_root(defaults: dict[str, Any], config_path: Path) -> Path:
    paths = _mapping(defaults.get("paths"), "defaults.paths")
    unknown_paths = set(paths) - VALID_PATH_KEYS
    if unknown_paths:
        _fail(f"Unknown path key(s) in defaults.paths: {', '.join(sorted(unknown_paths))}")

    override = os.environ.get("EBSTACK_LOG_ROOT")
    if override:
        return _path_value(override, "EBSTACK_LOG_ROOT", config_dir=config_path.parent)

    return _path_value(paths.get("log_root", "logs"), "defaults.paths.log_root", config_dir=config_path.parent)


def _load_architectures(data: dict[str, Any]) -> dict[str, Architecture]:
    architectures_data = _mapping(data.get("architectures"), "architectures")
    architectures: dict[str, Architecture] = {}

    for name, raw_arch in architectures_data.items():
        if not isinstance(name, str):
            _fail("architecture names must be strings")

        arch_data = _mapping(raw_arch, f"architectures.{name}")
        unknown_fields = set(arch_data) - VALID_ARCH_FIELDS
        if unknown_fields:
            _fail(
                f"Unknown field(s) in architectures.{name}: "
                f"{', '.join(sorted(unknown_fields))}"
            )

        cpu_vendor = arch_data.get("cpu_vendor")
        if cpu_vendor not in VALID_CPU_VENDORS:
            _fail(
                f"architectures.{name}.cpu_vendor must be one of: "
                f"{', '.join(sorted(VALID_CPU_VENDORS))}"
            )

        stack = arch_data.get("stack")
        if stack not in VALID_STACKS:
            _fail(
                f"architectures.{name}.stack must be one of: "
                f"{', '.join(sorted(VALID_STACKS))}"
            )

        cuda_cc = _string_list(
            arch_data.get("cuda_compute_capabilities", []),
            f"architectures.{name}.cuda_compute_capabilities",
        )
        if stack == "cpu" and cuda_cc:
            _fail(f"architectures.{name} is a CPU stack but has CUDA capabilities")
        if stack == "gpu" and not cuda_cc:
            _fail(f"architectures.{name} is a GPU stack but has no CUDA capabilities")

        architectures[name] = Architecture(
            name=name,
            cpu_vendor=cpu_vendor,
            stack=stack,
            cuda_compute_capabilities=cuda_cc,
            jobs=_jobs(arch_data.get("jobs", {}), f"architectures.{name}.jobs"),
        )

    return architectures


def _load_layers(data: dict[str, Any]) -> dict[str, Layer]:
    layers_data = _mapping(data.get("layers"), "layers")
    unknown_layers = set(layers_data) - VALID_LAYERS
    if unknown_layers:
        _fail(f"Unknown layer(s): {', '.join(sorted(unknown_layers))}")

    layers: dict[str, Layer] = {}
    for layer in sorted(VALID_LAYERS):
        layer_data = _mapping(layers_data.get(layer), f"layers.{layer}")
        unknown_fields = set(layer_data) - VALID_LAYER_FIELDS
        if unknown_fields:
            _fail(
                f"Unknown field(s) in layers.{layer}: "
                f"{', '.join(sorted(unknown_fields))}"
            )

        layers[layer] = Layer(
            name=layer,
            easyconfigs=_string_list(
                layer_data.get("easyconfigs", []), f"layers.{layer}.easyconfigs"
            ),
            options=_string_list(layer_data.get("options", []), f"layers.{layer}.options"),
        )

    return layers
