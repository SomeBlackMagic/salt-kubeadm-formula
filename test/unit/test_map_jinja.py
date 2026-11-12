"""Behavioral tests for rendering kubeadm/map.jinja."""

import pytest


def minimal_pillar(role="worker"):
    return {
        "kubeadm": {
            "version": "v1.25.16",
            "cluster": {
                "clusterName": "test",
                "controlPlaneEndpoint": "k8s-api:6443",
            },
            "node": {"role": role},
        }
    }


class TestMapJinja:
    def test_minimal_pillar_gets_defaults(self, salt_render):
        result = salt_render(minimal_pillar())
        assert result["version"] == "v1.25.16"
        assert result["cluster"]["clusterName"] == "test"
        assert result["kubelet"]["cgroupDriver"] == "systemd"

    def test_node_overrides_role(self, salt_render):
        pillar = minimal_pillar()
        pillar["kubeadm"]["roles"] = {
            "worker": {"patches": {"kubeletconfiguration": {"maxPods": 200}}}
        }
        pillar["kubeadm"]["node"]["patches"] = {
            "kubeletconfiguration": {"maxPods": 250}
        }
        result = salt_render(pillar)
        assert result["node"]["patches"]["kubeletconfiguration"]["maxPods"] == 250

    def test_role_is_selected_from_node_role(self, salt_render):
        pillar = minimal_pillar("control-plane")
        pillar["kubeadm"]["roles"] = {
            "control-plane": {"taints": {"control-plane": {"effect": "NoSchedule"}}},
            "worker": {"taints": {"worker": {"effect": "NoSchedule"}}},
        }
        result = salt_render(pillar)
        assert result["node"]["taints"] == {
            "control-plane": {"effect": "NoSchedule"}
        }

    def test_node_kubelet_violation_raises(self, salt_render):
        pillar = minimal_pillar()
        pillar["kubeadm"]["node"]["kubelet"] = {"clusterDNS": ["10.0.0.10"]}
        with pytest.raises(RuntimeError, match="clusterDNS"):
            salt_render(pillar)

    def test_node_cluster_subnet_violation_raises(self, salt_render):
        pillar = minimal_pillar("control-plane")
        pillar["kubeadm"]["node"]["cluster"] = {
            "networking": {"serviceSubnet": "10.0.0.0/8"}
        }
        with pytest.raises(RuntimeError, match="serviceSubnet"):
            salt_render(pillar)
