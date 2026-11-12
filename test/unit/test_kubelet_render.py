"""Behavioral rendering tests for kubeadm/kubelet.sls."""


def _render_defaults(render_sls, minimal_pillar, flags):
    pillar = minimal_pillar()
    pillar["kubeadm"]["node"]["flags"] = flags
    states = render_sls("kubeadm/kubelet", pillar)
    return next(
        state
        for state in states
        if state["fun"] == "file.managed"
        and state["name"] == "/etc/default/kubelet"
    )["contents"]


class TestKubeletSLS:
    def test_manages_unit_and_dropin(self, render_sls, minimal_pillar):
        states = render_sls("kubeadm/kubelet", minimal_pillar())
        managed = {
            state["name"]: state
            for state in states
            if state["fun"] == "file.managed"
        }
        assert "/etc/systemd/system/kubelet.service" in managed
        assert (
            "/etc/systemd/system/kubelet.service.d/10-kubeadm.conf" in managed
        )

    def test_defaults_contain_sorted_flags(self, render_sls, minimal_pillar):
        contents = _render_defaults(
            render_sls,
            minimal_pillar,
            {"node-ip": "10.0.0.12", "hostname-override": "master-1"},
        )
        assert "--hostname-override=master-1 --node-ip=10.0.0.12" in contents

    def test_boolean_flags_are_rendered(self, render_sls, minimal_pillar):
        contents = _render_defaults(
            render_sls,
            minimal_pillar,
            {"register-node": True, "rotate-certificates": False},
        )
        assert "--register-node" in contents
        assert "--register-node=true" not in contents
        assert "--rotate-certificates=false" in contents

    def test_service_is_enabled_but_not_started(self, render_sls, minimal_pillar):
        states = render_sls("kubeadm/kubelet", minimal_pillar())
        kubelet_services = [
            state
            for state in states
            if state["name"] == "kubelet" and state["fun"].startswith("service.")
        ]
        assert [state["fun"] for state in kubelet_services] == ["service.enabled"]

    def test_daemon_reload_watches_unit_files(self, render_sls, minimal_pillar):
        states = render_sls("kubeadm/kubelet", minimal_pillar())
        reload_state = next(
            state
            for state in states
            if state["fun"] == "module.run"
            and state.get("name") == "service.systemctl_reload"
        )
        assert len(reload_state["onchanges"]) == 2

    def test_does_not_use_cmd_run(self, render_sls, minimal_pillar):
        states = render_sls("kubeadm/kubelet", minimal_pillar())
        assert all(state["fun"] != "cmd.run" for state in states)

