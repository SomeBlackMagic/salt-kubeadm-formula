"""Shared fixtures for kubeadm formula unit tests."""

import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType
from unittest.mock import MagicMock

import jinja2
import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "_modules" / "kubeadm_util.py"
STATE_MODULE_PATH = ROOT / "_states" / "kubeadm.py"
RUNNER_MODULE_PATH = ROOT / "_runners" / "kubeadm.py"


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
def state_mod():
    """Load the custom kubeadm state with isolated Salt loader globals."""
    if not STATE_MODULE_PATH.is_file():
        pytest.fail(f"Required state module does not exist yet: {STATE_MODULE_PATH}")
    spec = importlib.util.spec_from_file_location(
        "kubeadm_state", STATE_MODULE_PATH
    )
    if spec is None or spec.loader is None:
        pytest.fail(f"Unable to load module spec from: {STATE_MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.__opts__ = {"test": False}
    module.__salt__ = {
        "cmd.run": MagicMock(),
        "cmd.run_all": MagicMock(),
        "cmd.which": MagicMock(return_value=None),
        "file.file_exists": MagicMock(return_value=False),
        "file.read": MagicMock(),
        "http.query": MagicMock(),
        "service.status": MagicMock(),
    }
    return module


@pytest.fixture
def runner_mod(monkeypatch):
    """Load the kubeadm runner with isolated Salt client modules."""
    if not RUNNER_MODULE_PATH.is_file():
        pytest.fail(f"Required runner does not exist yet: {RUNNER_MODULE_PATH}")

    salt_module = ModuleType("salt")
    client_module = ModuleType("salt.client")
    ssh_module = ModuleType("salt.client.ssh")
    client_module.LocalClient = MagicMock()
    ssh_module.SSHClient = MagicMock()
    client_module.ssh = ssh_module
    salt_module.client = client_module
    monkeypatch.setitem(sys.modules, "salt", salt_module)
    monkeypatch.setitem(sys.modules, "salt.client", client_module)
    monkeypatch.setitem(sys.modules, "salt.client.ssh", ssh_module)

    spec = importlib.util.spec_from_file_location(
        "kubeadm_runner", RUNNER_MODULE_PATH
    )
    if spec is None or spec.loader is None:
        pytest.fail(f"Unable to load runner spec from: {RUNNER_MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.__pillar__ = {
        "kubeadm": {
            "cluster": {"controlPlaneEndpoint": "k8s-api.test:6443"}
        }
    }
    return module


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


@pytest.fixture
def minimal_pillar():
    """Return a factory for the smallest valid Phase 1 pillar."""

    def build(version="v1.25.16", arch="amd64", role="worker"):
        return {
            "kubeadm": {
                "version": version,
                "arch": arch,
                "cluster": {
                    "clusterName": "test",
                    "controlPlaneEndpoint": "k8s-api.test:6443",
                    "networking": {
                        "serviceSubnet": "10.96.0.0/12",
                        "podSubnet": "10.244.0.0/16",
                        "dnsDomain": "cluster.local",
                    },
                },
                "kubelet": {
                    "clusterDNS": ["10.96.0.10"],
                    "clusterDomain": "cluster.local",
                },
                "node": {
                    "role": role,
                    "advertise_address": "10.0.0.12",
                    "flags": {},
                    "patches": {},
                },
            }
        }

    return build


@pytest.fixture
def render_sls():
    """Render an SLS and flatten its states into assertion-friendly mappings."""

    def render(sls, pillar, *, linked_paths=()):
        utility = _load_utility()
        linked_paths = set(linked_paths)

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
            "kubeadm_util.to_k8s": utility.to_k8s,
            "kubeadm_util.stale_version_dirs": utility.stale_version_dirs,
            "file.directory_exists": lambda _path: False,
            "file.find": lambda _path, **_kwargs: [],
            "file.is_link": lambda path: path in linked_paths,
            "log.debug": lambda _message: None,
        }
        environment = jinja2.Environment(
            loader=jinja2.FileSystemLoader(ROOT),
            extensions=["jinja2.ext.do"],
            undefined=jinja2.StrictUndefined,
        )
        environment.filters["yaml"] = lambda value, *_args, **_kwargs: yaml.safe_dump(
            value, sort_keys=False
        ).rstrip()
        environment.globals.update(salt=salt, pillar=pillar)
        environment.globals["raise"] = lambda message: (
            _ for _ in ()
        ).throw(RuntimeError(message))

        sls_path = ROOT / f"{sls.removesuffix('.sls')}.sls"
        if not sls_path.is_file():
            pytest.fail(f"Required SLS does not exist yet: {sls_path}")
        rendered = environment.get_template(
            str(sls_path.relative_to(ROOT))
        ).render()
        data = yaml.safe_load(rendered) or {}

        states = []
        for state_id, body in data.items():
            if state_id in {"include", "extend", "exclude"}:
                continue
            if not isinstance(body, dict):
                continue
            for function, arguments in body.items():
                state = {"id": state_id, "fun": function}
                for argument in arguments or []:
                    if isinstance(argument, dict):
                        state.update(argument)
                state.setdefault("name", state_id)
                states.append(state)
        return states

    return render


@pytest.fixture
def orch_pillar(minimal_pillar):
    """Return a minimal Salt-master pillar for bootstrap orchestration."""

    def build(transport="minion"):
        pillar = minimal_pillar(role="control-plane")
        pillar["kubeadm"].update(
            {
                "addons": {
                    "kube_proxy": {"managed_by": "kubeadm"},
                    "coredns": {"managed_by": "kubeadm"},
                },
                "orchestrate": {
                    "transport": transport,
                    "nodegroups": {
                        "masters": "k8s-masters",
                        "workers": "k8s-workers",
                    },
                    "source_ip": None,
                    "health": {"retries": 30, "interval": 10},
                },
            }
        )
        return pillar

    return build


@pytest.fixture
def render_orch():
    """Render an orchestration SLS with deterministic read-only runners."""

    def render(
        sls,
        pillar,
        *,
        test=False,
        cluster_exists=False,
        init_node="master-1",
    ):
        def pillar_get(path, default=None):
            value = pillar
            for part in path.split(":"):
                if not isinstance(value, dict) or part not in value:
                    return default
                value = value[part]
            return value

        def saltutil_runner(function, **_kwargs):
            if function == "kubeadm.cluster_exists":
                return cluster_exists
            if function == "kubeadm.pick_init_node":
                return init_node
            raise AssertionError(f"Unexpected render-time runner: {function}")

        environment = jinja2.Environment(
            loader=jinja2.FileSystemLoader(ROOT),
            extensions=["jinja2.ext.do"],
            undefined=jinja2.StrictUndefined,
        )
        environment.globals.update(
            salt={
                "pillar.get": pillar_get,
                "saltutil.runner": saltutil_runner,
            },
            pillar=pillar,
            opts={"test": test},
        )

        sls_path = ROOT / f"{sls.removesuffix('.sls')}.sls"
        if not sls_path.is_file():
            pytest.fail(f"Required orchestration SLS does not exist yet: {sls_path}")
        rendered = environment.get_template(
            str(sls_path.relative_to(ROOT))
        ).render()
        data = yaml.safe_load(rendered) or {}

        states = []
        for state_id, body in data.items():
            if not isinstance(body, dict):
                continue
            for function, arguments in body.items():
                state = {"id": state_id, "fun": function}
                for argument in arguments or []:
                    if isinstance(argument, dict):
                        state.update(argument)
                state.setdefault("name", state_id)
                states.append(state)
        return states

    return render
