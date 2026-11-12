"""Tests for the pure helpers in _modules/kubeadm_util.py."""

import copy

import pytest


NAMED_LIST_KEYS = [
    "containers",
    "initContainers",
    "env",
    "volumes",
    "volumeMounts",
    "ports",
]


class TestMergeLayers:
    def test_empty_layers_return_defaults(self, kubeadm_util):
        defaults = {"kubelet": {"cgroupDriver": "systemd"}}
        assert kubeadm_util.merge_layers(defaults, {}, {}, {}) == defaults

    def test_deep_recursive_merge(self, kubeadm_util):
        defaults = {
            "cluster": {"apiServer": {"extraArgs": {"a": "1"}, "certSANs": []}}
        }
        cluster = {"cluster": {"apiServer": {"extraArgs": {"b": "2"}}}}
        result = kubeadm_util.merge_layers(defaults, cluster, {}, {})
        assert result["cluster"]["apiServer"] == {
            "extraArgs": {"a": "1", "b": "2"},
            "certSANs": [],
        }

    def test_lists_are_replaced(self, kubeadm_util):
        defaults = {"kubelet": {"clusterDNS": ["10.96.0.10"]}}
        cluster = {"kubelet": {"clusterDNS": ["10.96.0.20", "10.96.0.21"]}}
        result = kubeadm_util.merge_layers(defaults, cluster, {}, {})
        assert result["kubelet"]["clusterDNS"] == ["10.96.0.20", "10.96.0.21"]

    def test_none_removes_key(self, kubeadm_util):
        defaults = {"kubelet": {"maxPods": 110, "cgroupDriver": "systemd"}}
        result = kubeadm_util.merge_layers(
            defaults, {}, {}, {"kubelet": {"maxPods": None}}
        )
        assert result["kubelet"] == {"cgroupDriver": "systemd"}

    def test_node_has_highest_priority(self, kubeadm_util):
        layers = [
            {"kubelet": {"maxPods": value}} for value in (110, 150, 200, 250)
        ]
        assert kubeadm_util.merge_layers(*layers)["kubelet"]["maxPods"] == 250

    def test_upper_layers_add_keys(self, kubeadm_util):
        result = kubeadm_util.merge_layers(
            {"kubelet": {}}, {}, {}, {"kubelet": {"customSetting": "value"}}
        )
        assert result["kubelet"]["customSetting"] == "value"

    def test_inputs_are_not_mutated(self, kubeadm_util):
        layers = (
            {"kubelet": {"maxPods": 110}},
            {"kubelet": {"maxPods": 200}},
            {},
            {},
        )
        original = copy.deepcopy(layers)
        kubeadm_util.merge_layers(*layers)
        assert layers == original


class TestToK8s:
    def test_containers_become_named_list(self, kubeadm_util):
        obj = {
            "containers": {
                "nginx": {"image": "nginx:1.25"},
                "sidecar": {"image": "sidecar:1.0"},
            }
        }
        result = kubeadm_util.to_k8s(obj, NAMED_LIST_KEYS)
        assert {item["name"] for item in result["containers"]} == {
            "nginx",
            "sidecar",
        }

    def test_nested_named_lists(self, kubeadm_util):
        obj = {
            "containers": {
                "app": {
                    "env": {"FOO": {"value": "bar"}},
                    "ports": {"http": {"containerPort": 8080}},
                }
            }
        }
        container = kubeadm_util.to_k8s(obj, NAMED_LIST_KEYS)["containers"][0]
        assert container["env"] == [{"name": "FOO", "value": "bar"}]
        assert container["ports"] == [{"name": "http", "containerPort": 8080}]

    def test_non_named_dicts_are_unchanged(self, kubeadm_util):
        obj = {"metadata": {"labels": {"app": "nginx"}}}
        assert kubeadm_util.to_k8s(obj, NAMED_LIST_KEYS) == obj

    def test_existing_lists_are_unchanged(self, kubeadm_util):
        obj = {"containers": [{"name": "app", "image": "app:1.0"}]}
        assert kubeadm_util.to_k8s(obj, NAMED_LIST_KEYS) == obj

    def test_empty_named_dict_becomes_empty_list(self, kubeadm_util):
        result = kubeadm_util.to_k8s({"containers": {}}, NAMED_LIST_KEYS)
        assert result["containers"] == []

    def test_input_is_not_mutated(self, kubeadm_util):
        obj = {"containers": {"app": {"image": "app:1.0"}}}
        original = copy.deepcopy(obj)
        kubeadm_util.to_k8s(obj, NAMED_LIST_KEYS)
        assert obj == original


class TestExtraArgs:
    def test_v1beta3_returns_string_map(self, kubeadm_util):
        result = kubeadm_util.extra_args({"maxage": 30, "enabled": True}, "v1beta3")
        assert result == {"maxage": "30", "enabled": "true"}

    def test_v1beta4_returns_name_value_list(self, kubeadm_util):
        result = kubeadm_util.extra_args({"flag-a": "a", "flag-b": 42}, "v1beta4")
        assert {tuple(sorted(item.items())) for item in result} == {
            (("name", "flag-a"), ("value", "a")),
            (("name", "flag-b"), ("value", "42")),
        }

    @pytest.mark.parametrize(
        ("api_version", "expected"), [("v1beta3", {}), ("v1beta4", [])]
    )
    def test_empty_args(self, kubeadm_util, api_version, expected):
        assert kubeadm_util.extra_args({}, api_version) == expected

    def test_unknown_version_raises(self, kubeadm_util):
        with pytest.raises(ValueError, match="api_version"):
            kubeadm_util.extra_args({"a": "b"}, "v1beta99")


class TestCheckDenylist:
    def test_clean_layer_has_no_violations(self, kubeadm_util):
        layer = {"kubelet": {"maxPods": 200}}
        denylist = {"kubelet": ["clusterDNS", "cgroupDriver"]}
        assert kubeadm_util.check_denylist(layer, denylist) == []

    def test_violation_includes_path_and_value(self, kubeadm_util):
        layer = {"kubelet": {"clusterDNS": ["10.0.0.10"]}}
        result = kubeadm_util.check_denylist(layer, {"kubelet": ["clusterDNS"]})
        assert result == [
            {"path": "kubelet.clusterDNS", "value": ["10.0.0.10"]}
        ]

    def test_multiple_violations_are_reported(self, kubeadm_util):
        layer = {
            "kubelet": {"clusterDNS": ["10.0.0.10"], "cgroupDriver": "cgroupfs"}
        }
        result = kubeadm_util.check_denylist(
            layer, {"kubelet": ["clusterDNS", "cgroupDriver"]}
        )
        assert {item["path"] for item in result} == {
            "kubelet.clusterDNS",
            "kubelet.cgroupDriver",
        }

    def test_nested_dot_path(self, kubeadm_util):
        layer = {"cluster": {"networking": {"serviceSubnet": "10.0.0.0/8"}}}
        result = kubeadm_util.check_denylist(
            layer, {"cluster": ["networking.serviceSubnet"]}
        )
        assert result == [
            {
                "path": "cluster.networking.serviceSubnet",
                "value": "10.0.0.0/8",
            }
        ]

    @pytest.mark.parametrize(("layer", "denylist"), [({}, {"kubelet": ["x"]}), ({}, {})])
    def test_empty_inputs(self, kubeadm_util, layer, denylist):
        assert kubeadm_util.check_denylist(layer, denylist) == []

    def test_none_still_counts_as_present(self, kubeadm_util):
        result = kubeadm_util.check_denylist(
            {"kubelet": {"clusterDNS": None}}, {"kubelet": ["clusterDNS"]}
        )
        assert result == [{"path": "kubelet.clusterDNS", "value": None}]


class TestStaleVersionDirs:
    def test_keeps_current_and_newest_other_version(self, kubeadm_util):
        paths = [
            "/opt/kubernetes/v1.24.0",
            "/opt/kubernetes/v1.25.16",
            "/opt/kubernetes/v1.26.0",
        ]
        result = kubeadm_util.stale_version_dirs(paths, "v1.25.16")
        assert result == ["/opt/kubernetes/v1.24.0"]

    def test_current_need_not_exist_yet(self, kubeadm_util):
        paths = ["/opt/kubernetes/v1.24.0", "/opt/kubernetes/v1.25.16"]
        result = kubeadm_util.stale_version_dirs(paths, "v1.26.0")
        assert result == ["/opt/kubernetes/v1.24.0"]

    def test_ignores_non_semver_directories(self, kubeadm_util):
        paths = ["/opt/kubernetes/cache", "/opt/kubernetes/v1.25.16"]
        assert kubeadm_util.stale_version_dirs(paths, "v1.25.16") == []

    def test_rejects_invalid_current_version(self, kubeadm_util):
        with pytest.raises(ValueError, match="invalid Kubernetes version"):
            kubeadm_util.stale_version_dirs([], "latest")
