"""Contract test for the Phase 1 node meta-state."""

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def test_init_includes_phase_one_states():
    contents = (ROOT / "kubeadm" / "init.sls").read_text(encoding="utf-8")
    without_jinja_statements = "\n".join(
        line for line in contents.splitlines() if not line.lstrip().startswith("{")
    )
    data = yaml.safe_load(without_jinja_statements)
    assert data["include"] == [
        "kubeadm.binaries",
        "kubeadm.kubelet",
        "kubeadm.config",
    ]
