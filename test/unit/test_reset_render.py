"""Rendering contract tests for kubeadm/reset.sls."""


class TestKubeadmResetSLS:
    def test_refuses_without_confirmation(self, render_sls, minimal_pillar):
        states = render_sls("kubeadm/reset", minimal_pillar())

        assert len(states) == 1
        assert states[0]["fun"] == "test.fail_without_changes"
        assert "confirm_reset" in states[0]["comment"]

    def test_confirmed_reset_cleans_cluster_data_in_order(
        self, render_sls, minimal_pillar
    ):
        pillar = minimal_pillar()
        pillar["kubeadm"]["confirm_reset"] = True

        states = render_sls("kubeadm/reset", pillar)

        assert all(state["fun"] != "test.fail_without_changes" for state in states)
        reset = next(state for state in states if state["fun"] == "cmd.run")
        assert reset["name"] == "kubeadm reset -f"
        assert reset["onlyif"] == (
            "test -f /etc/kubernetes/admin.conf -o "
            "-f /etc/kubernetes/kubelet.conf"
        )
        cleanup = next(state for state in states if state["fun"] == "file.absent")
        assert {
            "/etc/kubernetes",
            "/var/lib/etcd",
            "/etc/cni/net.d",
        } <= set(cleanup["names"])
        assert cleanup["require"] == [{"cmd": "kubeadm_reset"}]
        iptables = next(
            state
            for state in states
            if state["fun"] == "module.run" and state["name"] == "iptables.flush"
        )
        assert iptables["require"] == [{"file": "kubeadm_cleanup"}]
