"""Behavioral tests for the Phase 2 kubeadm runner."""

from unittest.mock import MagicMock

import pytest


class TestTransport:
    def test_minion_uses_local_client(self, runner_mod):
        assert runner_mod._client("minion") is (
            runner_mod.salt.client.LocalClient.return_value
        )

    def test_ssh_uses_ssh_client(self, runner_mod):
        assert runner_mod._client("ssh") is (
            runner_mod.salt.client.ssh.SSHClient.return_value
        )

    def test_unknown_transport_is_rejected(self, runner_mod):
        with pytest.raises(ValueError, match="Unknown transport"):
            runner_mod._client("telnet")

    def test_cmd_forwards_arguments_to_selected_client(self, runner_mod):
        client = MagicMock()
        runner_mod._client = MagicMock(return_value=client)

        result = runner_mod._cmd(
            "master-1",
            "file.file_exists",
            arg=["/etc/kubernetes/admin.conf"],
            kwarg={"follow_symlinks": True},
            transport="ssh",
        )

        assert result is client.cmd.return_value
        client.cmd.assert_called_once_with(
            "master-1",
            "file.file_exists",
            arg=["/etc/kubernetes/admin.conf"],
            kwarg={"follow_symlinks": True},
        )


class TestClusterExists:
    def test_no_cluster_signal_returns_false(self, runner_mod):
        runner_mod._http_check = MagicMock(return_value=False)
        runner_mod._cmd = MagicMock(
            side_effect=[{"master-1": False}, {"master-1": False}]
        )

        assert runner_mod.cluster_exists("k8s-masters", "minion") is False

    def test_ready_endpoint_is_a_cluster_signal(self, runner_mod):
        runner_mod._http_check = MagicMock(return_value=True)
        runner_mod._cmd = MagicMock(return_value={"master-1": False})

        assert runner_mod.cluster_exists("k8s-masters", "minion") is True

    @pytest.mark.parametrize(
        "responses",
        [
            [{"master-1": True}, {"master-1": False}],
            [{"master-1": False}, {"master-1": True}],
        ],
        ids=["admin-conf", "etcd-manifest"],
    )
    def test_local_file_signal_detects_cluster_when_lb_is_down(
        self, runner_mod, responses
    ):
        runner_mod._http_check = MagicMock(return_value=False)
        runner_mod._cmd = MagicMock(side_effect=responses)

        assert runner_mod.cluster_exists("k8s-masters", "minion") is True

    def test_checks_endpoint_and_both_local_files(self, runner_mod):
        runner_mod._http_check = MagicMock(return_value=True)
        runner_mod._cmd = MagicMock(return_value={"master-1": False})

        runner_mod.cluster_exists("k8s-masters", "minion")

        runner_mod._http_check.assert_called_once_with(
            "https://k8s-api.test:6443/readyz"
        )
        assert runner_mod._cmd.call_count == 2
        checked_paths = [call.kwargs["arg"][0] for call in runner_mod._cmd.call_args_list]
        assert checked_paths == [
            "/etc/kubernetes/admin.conf",
            "/etc/kubernetes/manifests/etcd.yaml",
        ]


class TestEndpointPrecheck:
    def test_resolvable_open_endpoint_passes(self, runner_mod):
        runner_mod.socket = MagicMock()
        runner_mod.socket.getaddrinfo.return_value = [
            (2, 1, 6, "", ("10.0.0.10", 6443))
        ]

        assert runner_mod.endpoint_precheck("k8s-api.test:6443") is True
        runner_mod.socket.getaddrinfo.assert_called()
        runner_mod.socket.create_connection.assert_called_once_with(
            ("k8s-api.test", 6443), timeout=3.0
        )

    def test_unreachable_endpoint_fails(self, runner_mod):
        runner_mod.socket = MagicMock()
        runner_mod.socket.getaddrinfo.return_value = [
            (2, 1, 6, "", ("10.0.0.10", 6443))
        ]
        runner_mod.socket.create_connection.side_effect = OSError(
            "connection refused"
        )

        with pytest.raises(RuntimeError, match="Unable to connect"):
            runner_mod.endpoint_precheck("k8s-api.test:6443")


class TestPickInitNode:
    def test_source_override_wins(self, runner_mod):
        runner_mod._nodegroup_members = MagicMock(return_value=["master-1"])

        assert runner_mod.pick_init_node("k8s-masters", "master-3") == "master-3"
        runner_mod._nodegroup_members.assert_not_called()

    def test_first_nodegroup_member_is_default(self, runner_mod):
        runner_mod._nodegroup_members = MagicMock(
            return_value=["master-1", "master-2"]
        )

        assert runner_mod.pick_init_node("k8s-masters", None) == "master-1"

    def test_empty_nodegroup_is_rejected(self, runner_mod):
        runner_mod._nodegroup_members = MagicMock(return_value=[])

        with pytest.raises(RuntimeError, match="No nodes"):
            runner_mod.pick_init_node("k8s-masters")


class TestPickSource:
    def test_picks_first_healthy_master(self, runner_mod):
        runner_mod._nodegroup_members = MagicMock(
            return_value=["master-1", "master-2", "master-3"]
        )
        runner_mod._check_master_health = MagicMock(side_effect=[False, True])

        assert runner_mod.pick_source("k8s-masters", "minion") == "master-2"

    def test_healthy_source_override_wins(self, runner_mod):
        runner_mod._check_master_health = MagicMock(return_value=True)

        assert (
            runner_mod.pick_source("k8s-masters", "ssh", source_ip="master-3")
            == "master-3"
        )

    def test_unhealthy_source_override_is_rejected(self, runner_mod):
        runner_mod._check_master_health = MagicMock(return_value=False)

        with pytest.raises(RuntimeError, match="No healthy"):
            runner_mod.pick_source(
                "k8s-masters", "minion", source_ip="master-3"
            )

    def test_no_healthy_master_is_rejected(self, runner_mod):
        runner_mod._nodegroup_members = MagicMock(
            return_value=["master-1", "master-2"]
        )
        runner_mod._check_master_health = MagicMock(return_value=False)

        with pytest.raises(RuntimeError, match="No healthy"):
            runner_mod.pick_source("k8s-masters", "minion")
