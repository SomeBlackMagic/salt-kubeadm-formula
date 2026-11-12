"""Shared fixtures for kubeadm formula unit tests."""

import importlib.util
import json
from pathlib import Path

import jinja2
import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "_modules" / "kubeadm_util.py"


def _load_utility():
    if not MODULE_PATH.is_file():
        pytest.fail(f"Required module does not exist yet: {MODULE_PATH}")
    spec = importlib.util.spec_from_file_location("kubeadm_util", MODULE_PATH)
    if spec is None or spec.loader is None:
        pytest.fail(f"Unable to load module spec from: {MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def kubeadm_util():
    """Load the execution module without requiring the Salt loader."""
    return _load_utility()


@pytest.fixture
def salt_render():
    """Render map.jinja with the small subset of Salt that it consumes."""

    def render(pillar):
        utility = _load_utility()

        def pillar_get(path, default=None):
            value = pillar
            for part in path.split(":"):
                if not isinstance(value, dict) or part not in value:
                    return default
                value = value[part]
            return value

        def slsutil_renderer(path=None, **_kwargs):
            relative_path = str(path).removeprefix("salt://")
            with (ROOT / relative_path).open(encoding="utf-8") as stream:
                return yaml.safe_load(stream)

        salt = {
            "pillar.get": pillar_get,
            "slsutil.renderer": slsutil_renderer,
            "kubeadm_util.merge_layers": utility.merge_layers,
            "kubeadm_util.check_denylist": utility.check_denylist,
            "log.debug": lambda _message: None,
        }
        environment = jinja2.Environment(
            loader=jinja2.FileSystemLoader(ROOT),
            extensions=["jinja2.ext.do"],
            undefined=jinja2.StrictUndefined,
        )
        environment.globals.update(salt=salt, pillar=pillar)
        environment.globals["raise"] = lambda message: (
            _ for _ in ()
        ).throw(RuntimeError(message))
        wrapper = environment.from_string(
            "{% from 'kubeadm/map.jinja' import kubeadm with context %}"
            "{{ kubeadm | tojson }}"
        )
        return json.loads(wrapper.render())

    return render
