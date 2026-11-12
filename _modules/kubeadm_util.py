"""Pure helpers shared by kubeadm formula renderers."""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping


def _merge(base, overlay):
    result = copy.deepcopy(base)
    for key, value in overlay.items():
        if value is None:
            result.pop(key, None)
        elif isinstance(value, Mapping) and isinstance(result.get(key), Mapping):
            result[key] = _merge(dict(result[key]), value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def merge_layers(defaults, cluster, role, node):
    """Merge four mappings recursively without mutating the inputs."""
    result = {}
    for layer in (defaults, cluster, role, node):
        if not isinstance(layer, Mapping):
            raise TypeError("all layers must be mappings")
        result = _merge(result, layer)
    return result


def to_k8s(obj, named_list_keys):
    """Convert dict-based Kubernetes named lists into real lists."""
    keys = set(named_list_keys)

    def convert(value, parent_key=None):
        if isinstance(value, Mapping):
            if parent_key in keys:
                converted = []
                for name, item in value.items():
                    if not isinstance(item, Mapping):
                        raise TypeError(
                            f"named-list item {parent_key}.{name} must be a mapping"
                        )
                    mapped_item = convert(item)
                    mapped_item["name"] = name
                    converted.append(mapped_item)
                return converted
            return {key: convert(item, key) for key, item in value.items()}
        if isinstance(value, list):
            return [convert(item) for item in value]
        return copy.deepcopy(value)

    return convert(obj)


def _stringify(value):
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)


def extra_args(args, api_version):
    """Render extraArgs for kubeadm v1beta3 or v1beta4."""
    if not isinstance(args, Mapping):
        raise TypeError("args must be a mapping")
    if api_version == "v1beta3":
        return {name: _stringify(value) for name, value in args.items()}
    if api_version == "v1beta4":
        return [
            {"name": name, "value": _stringify(value)}
            for name, value in args.items()
        ]
    raise ValueError(f"unsupported api_version: {api_version}")


def check_denylist(layer, denylist):
    """Return denylisted dot paths that are present in a layer."""
    if not isinstance(layer, Mapping) or not isinstance(denylist, Mapping):
        raise TypeError("layer and denylist must be mappings")
    violations = []
    missing = object()
    for section, paths in denylist.items():
        for relative_path in paths:
            parts = [section, *relative_path.split(".")]
            value = layer
            for part in parts:
                if not isinstance(value, Mapping):
                    value = missing
                    break
                value = value.get(part, missing)
                if value is missing:
                    break
            if value is not missing:
                violations.append({"path": ".".join(parts), "value": value})
    return violations


def stale_version_dirs(paths, current_version, keep=2):
    """Return old version directories, always preserving the requested version."""
    if keep < 1:
        raise ValueError("keep must be at least 1")

    version_pattern = re.compile(r"^v(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$")

    def version_key(version):
        match = version_pattern.match(version)
        if match is None:
            return None
        return tuple(int(part) for part in match.groups())

    candidates = {}
    for path in paths:
        name = str(path).rstrip("/").rsplit("/", 1)[-1]
        key = version_key(name)
        if key is not None:
            candidates[name] = (key, str(path).rstrip("/"))

    current_key = version_key(current_version)
    if current_key is None:
        raise ValueError(f"invalid Kubernetes version: {current_version}")
    candidates.setdefault(current_version, (current_key, None))

    retained = {current_version}
    other_versions = sorted(
        (
            (name, value)
            for name, value in candidates.items()
            if name != current_version
        ),
        key=lambda item: item[1][0],
        reverse=True,
    )
    retained.update(name for name, _value in other_versions[: keep - 1])
    return [
        path
        for name, (_key, path) in candidates.items()
        if path is not None and name not in retained
    ]
