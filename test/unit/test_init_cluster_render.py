"""Rendering contract tests for kubeadm/init_cluster.sls."""


class TestInitClusterSLS:
    def test_preflight_phase_only_renders_preflight(
        self, render_sls, minimal_pillar
    ):
        pillar = minimal_pillar(role="control-plane")
        pillar["kubeadm"]["_phase"] = "preflight"

        states = render_sls("kubeadm/init_cluster", pillar)

        assert [state["fun"] for state in states] == ["kubeadm.preflight"]
        assert states[0]["config"].endswith("/init.yaml")
        assert states[0]["pull_images"] is True

    def test_init_phase_passes_init_parameters(
        self, render_sls, minimal_pillar
    ):
        pillar = minimal_pillar(role="control-plane")
        pillar["kubeadm"]["_phase"] = "init"
        pillar["kubeadm"]["_skip_phases"] = ["addon/kube-proxy"]

        states = render_sls("kubeadm/init_cluster", pillar)

        assert [state["fun"] for state in states] == ["kubeadm.initialized"]
        assert states[0]["config"].endswith("/init.yaml")
        assert states[0]["patches"] == "/etc/kubernetes/patches"
        assert states[0]["upload_certs"] is True
        assert states[0]["skip_phases"] == ["addon/kube-proxy"]
        assert "kind: InitConfiguration" in states[0]["rendered_config"]

    def test_health_phase_uses_timing_without_node_ready(
        self, render_sls, minimal_pillar
    ):
        pillar = minimal_pillar(role="control-plane")
        pillar["kubeadm"]["_phase"] = "health"
        pillar["kubeadm"]["orchestrate"] = {
            "health": {"retries": 7, "interval": 2}
        }

        states = render_sls("kubeadm/init_cluster", pillar)

        assert [state["fun"] for state in states] == ["kubeadm.healthy"]
        assert states[0]["checks"] == ["readyz", "etcd"]
        assert states[0]["retries"] == 7
        assert states[0]["interval"] == 2

    def test_unknown_phase_fails_closed(self, render_sls, minimal_pillar):
        pillar = minimal_pillar(role="control-plane")
        pillar["kubeadm"]["_phase"] = "typo"

        states = render_sls("kubeadm/init_cluster", pillar)

        assert [state["fun"] for state in states] == [
            "test.fail_without_changes"
        ]
        assert "unknown" in states[0]["comment"].lower()
