"""Rendering contract tests for the Phase 2 bootstrap orchestration."""


def _state(states, state_id):
    return next(state for state in states if state["id"] == state_id)


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

    def test_major_steps_have_strict_requisite_chain(
        self, render_orch, orch_pillar
    ):
        states = render_orch("orch/kubeadm/bootstrap", orch_pillar())

        assert _state(states, "endpoint_precheck")["require"] == [
            {"test": "bootstrap_guard"}
        ]
        assert _state(states, "containerd_on_init")["require"] == [
            {"salt": "endpoint_precheck"}
        ]
        assert _state(states, "node_states_init")["require"] == [
            {"salt": "containerd_on_init"}
        ]
        assert _state(states, "preflight")["require"] == [
            {"salt": "node_states_init"}
        ]
        assert _state(states, "init_cluster")["require"] == [
            {"salt": "preflight"}
        ]
        assert _state(states, "init_health")["require"] == [
            {"salt": "init_cluster"}
        ]

    def test_endpoint_and_node_targets_are_forwarded(
        self, render_orch, orch_pillar
    ):
        states = render_orch(
            "orch/kubeadm/bootstrap", orch_pillar(), init_node="master-2"
        )

        endpoint = _state(states, "endpoint_precheck")
        assert endpoint["name"] == "kubeadm.endpoint_precheck"
        assert endpoint["endpoint"] == "k8s-api.test:6443"
        state_steps = [state for state in states if state["fun"] == "salt.state"]
        assert state_steps
        assert all(state["tgt"] == "master-2" for state in state_steps)

    def test_cluster_detection_receives_source_override(
        self, render_orch, orch_pillar
    ):
        pillar = orch_pillar()
        pillar["kubeadm"]["orchestrate"]["source_ip"] = "master-1"
        render_orch("orch/kubeadm/bootstrap", pillar)

        call = render_orch.runner_calls[0]
        assert call[0] == "kubeadm.cluster_exists"
        assert call[1]["source_ip"] == "master-1"

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

    def test_completion_event_is_emitted_after_health(
        self, render_orch, orch_pillar
    ):
        states = render_orch("orch/kubeadm/bootstrap", orch_pillar())
        event = _state(states, "bootstrap_done")

        assert event["fun"] == "salt.runner"
        assert event["name"] == "event.send"
        assert event["tag"] == "kubeadm/bootstrap/done/master-1"
        assert event["require"] == [{"salt": "init_health"}]

    def test_test_mode_does_not_emit_events(
        self, render_orch, orch_pillar
    ):
        states = render_orch(
            "orch/kubeadm/bootstrap", orch_pillar(), test=True
        )

        assert not any(
            state["fun"] == "salt.runner"
            and state.get("name") == "event.send"
            for state in states
        )
