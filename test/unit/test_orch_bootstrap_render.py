"""Rendering contract tests for the Phase 2 bootstrap orchestration."""


def _state(states, state_id):
    return next(state for state in states if state["id"] == state_id)


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

    def test_init_phase_passes_skip_phases(self, render_sls, minimal_pillar):
        pillar = minimal_pillar(role="control-plane")
        pillar["kubeadm"]["_phase"] = "init"
        pillar["kubeadm"]["_skip_phases"] = ["addon/kube-proxy"]

        states = render_sls("kubeadm/init_cluster", pillar)

        assert [state["fun"] for state in states] == ["kubeadm.initialized"]
        assert states[0]["skip_phases"] == ["addon/kube-proxy"]
        assert "kind: InitConfiguration" in states[0]["rendered_config"]

    def test_health_phase_does_not_require_node_ready(
        self, render_sls, minimal_pillar
    ):
        pillar = minimal_pillar(role="control-plane")
        pillar["kubeadm"]["_phase"] = "health"

        states = render_sls("kubeadm/init_cluster", pillar)

        assert [state["fun"] for state in states] == ["kubeadm.healthy"]
        assert states[0]["checks"] == ["readyz", "etcd"]


class TestOrchBootstrap:
    def test_cluster_guard_is_first_and_rejects_existing_cluster(
        self, render_orch, orch_pillar
    ):
        states = render_orch(
            "orch/kubeadm/bootstrap", orch_pillar(), cluster_exists=True
        )

        assert states[0]["id"] == "bootstrap_guard"
        assert states[0]["fun"] == "test.fail_without_changes"
        assert "cluster exists" in states[0]["name"].lower()

    def test_major_steps_are_ordered(self, render_orch, orch_pillar):
        states = render_orch("orch/kubeadm/bootstrap", orch_pillar())
        ids = [state["id"] for state in states]

        expected = [
            "bootstrap_guard",
            "endpoint_precheck",
            "containerd_on_init",
            "node_states_init",
            "preflight",
            "init_cluster",
            "init_health",
            "bootstrap_done",
        ]
        positions = [ids.index(state_id) for state_id in expected]
        assert positions == sorted(positions)

    def test_test_mode_is_passed_to_every_nested_state(
        self, render_orch, orch_pillar
    ):
        states = render_orch("orch/kubeadm/bootstrap", orch_pillar(), test=True)
        state_steps = [state for state in states if state["fun"] == "salt.state"]

        assert state_steps
        assert all(state.get("test") is True for state in state_steps)

    def test_ssh_transport_marks_every_nested_state(
        self, render_orch, orch_pillar
    ):
        states = render_orch(
            "orch/kubeadm/bootstrap", orch_pillar(transport="ssh")
        )
        state_steps = [state for state in states if state["fun"] == "salt.state"]

        assert state_steps
        assert all(state.get("ssh") is True for state in state_steps)

    def test_external_addons_are_skipped_during_init(
        self, render_orch, orch_pillar
    ):
        pillar = orch_pillar()
        pillar["kubeadm"]["addons"]["kube_proxy"]["managed_by"] = "external"
        pillar["kubeadm"]["addons"]["coredns"]["managed_by"] = "external"

        states = render_orch("orch/kubeadm/bootstrap", pillar)
        init = _state(states, "init_cluster")

        assert init["pillar"]["kubeadm"]["_skip_phases"] == [
            "addon/kube-proxy",
            "addon/coredns",
        ]

    def test_fixture_preflight_exceptions_are_forwarded(
        self, render_orch, orch_pillar
    ):
        pillar = orch_pillar()
        pillar["kubeadm"]["_ignore_preflight_errors"] = [
            "SystemVerification"
        ]

        states = render_orch("orch/kubeadm/bootstrap", pillar)

        for state_id in ("preflight", "init_cluster"):
            state = _state(states, state_id)
            assert state["pillar"]["kubeadm"][
                "_ignore_preflight_errors"
            ] == ["SystemVerification"]

    def test_completion_event_is_emitted(self, render_orch, orch_pillar):
        states = render_orch("orch/kubeadm/bootstrap", orch_pillar())
        event_tags = {
            state["tag"]
            for state in states
            if state["fun"] == "salt.runner"
            and state.get("name") == "event.send"
        }

        assert "kubeadm/bootstrap/done/master-1" in event_tags
