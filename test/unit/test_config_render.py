"""Behavioral rendering tests for kubeadm/config.sls."""

import yaml


class TestConfigSLS:
    def test_creates_config_and_clean_patches_directories(
        self, render_sls, minimal_pillar
    ):
        states = render_sls("kubeadm/config", minimal_pillar())
        directories = {
            state["name"]: state
            for state in states
            if state["fun"] == "file.directory"
        }
        assert "/etc/kubernetes/kubeadm" in directories
        assert directories["/etc/kubernetes/patches"]["clean"] is True

    def test_manages_non_secret_config_documents(self, render_sls, minimal_pillar):
        states = render_sls("kubeadm/config", minimal_pillar(role="control-plane"))
        managed_names = {
            state["name"]
            for state in states
            if state["fun"] == "file.managed"
        }
        assert {
            "/etc/kubernetes/kubeadm/init.yaml",
            "/etc/kubernetes/kubeadm/cluster.yaml",
            "/etc/kubernetes/kubeadm/kubelet.yaml",
        } <= managed_names
        assert "/etc/kubernetes/kubeadm/join.yaml" not in managed_names

    def test_join_config_is_only_rendered_with_runtime_token(
        self, render_sls, minimal_pillar
    ):
        pillar = minimal_pillar()
        pillar["kubeadm"]["join"] = {
            "token": "abcdef.0123456789abcdef",
            "ca_cert_hash": "sha256:deadbeef",
        }
        states = render_sls("kubeadm/config", pillar)
        join = next(
            state
            for state in states
            if state["fun"] == "file.managed"
            and state["name"] == "/etc/kubernetes/kubeadm/join.yaml"
        )
        assert str(join["mode"]) in {"0600", "600", "384"}
        assert join.get("show_changes") is False

    def test_patch_is_mode_0600_and_named_lists_are_converted(
        self, render_sls, minimal_pillar
    ):
        pillar = minimal_pillar()
        pillar["kubeadm"]["node"]["patches"] = {
            "kube-apiserver": {
                "spec": {
                    "containers": {
                        "kube-apiserver": {
                            "env": {"GOMAXPROCS": {"value": "4"}}
                        }
                    }
                }
            }
        }
        states = render_sls("kubeadm/config", pillar)
        patch = next(
            state
            for state in states
            if state["fun"] == "file.managed"
            and state["name"].endswith("/kube-apiserver+strategic.yaml")
        )
        assert str(patch["mode"]) in {"0600", "600", "384"}
        content = yaml.safe_load(patch["contents"])
        containers = content["spec"]["containers"]
        assert containers == [
            {
                "env": [{"value": "4", "name": "GOMAXPROCS"}],
                "name": "kube-apiserver",
            }
        ]

    def test_empty_patches_manage_no_patch_files(self, render_sls, minimal_pillar):
        states = render_sls("kubeadm/config", minimal_pillar())
        assert not any(
            state["fun"] == "file.managed" and "+strategic.yaml" in state["name"]
            for state in states
        )

    def test_does_not_use_cmd_run(self, render_sls, minimal_pillar):
        states = render_sls("kubeadm/config", minimal_pillar())
        assert all(state["fun"] != "cmd.run" for state in states)

    def test_templates_receive_map_as_explicit_context(
        self, render_sls, minimal_pillar
    ):
        states = render_sls("kubeadm/config", minimal_pillar())
        configs = [
            state
            for state in states
            if state["fun"] == "file.managed" and state.get("template") == "jinja"
        ]
        assert configs
        assert all("kubeadm" in state["context"] for state in configs)

