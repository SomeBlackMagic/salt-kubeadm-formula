"""Behavioral tests for the Phase 2 custom kubeadm states."""


class TestKubeadmInitialized:
    def test_already_initialized_no_changes(self, state_mod):
        state_mod.__salt__["file.file_exists"].return_value = True

        result = state_mod.initialized(
            name="init", config="/etc/kubernetes/kubeadm/init.yaml"
        )

        assert result["result"] is True
        assert result["changes"] == {}
        assert "already initialized" in result["comment"].lower()
        state_mod.__salt__["file.file_exists"].assert_called_once_with(
            "/etc/kubernetes/admin.conf"
        )
        state_mod.__salt__["cmd.run"].assert_not_called()
        state_mod.__salt__["cmd.run_all"].assert_not_called()

    def test_init_runs_kubeadm(self, state_mod):
        state_mod.__salt__["cmd.run_all"].return_value = {
            "retcode": 0,
            "stdout": "initialized",
            "stderr": "",
        }

        result = state_mod.initialized(
            name="init",
            config="/etc/kubernetes/kubeadm/init.yaml",
            upload_certs=True,
            ignore_preflight_errors=["SystemVerification"],
        )

        assert result["result"] is True
        assert result["changes"]
        state_mod.__salt__["cmd.run"].assert_not_called()
        state_mod.__salt__["cmd.run_all"].assert_called_once_with(
            [
                "kubeadm",
                "init",
                "--config",
                "/etc/kubernetes/kubeadm/init.yaml",
                "--upload-certs",
                "--ignore-preflight-errors=SystemVerification",
            ],
            python_shell=False,
            redirect_stderr=False,
        )

    def test_init_test_mode_runs_dry_run_when_kubeadm_exists(self, state_mod):
        state_mod.__opts__["test"] = True
        state_mod.__salt__["cmd.which"].return_value = "/usr/local/bin/kubeadm"
        state_mod.__salt__["cmd.run_all"].return_value = {
            "retcode": 0,
            "stdout": "dry-run output",
            "stderr": "",
        }
        rendered = "apiVersion: kubeadm.k8s.io/v1beta4\nkind: InitConfiguration\n"

        result = state_mod.initialized(
            name="init",
            config="/etc/kubernetes/kubeadm/init.yaml",
            rendered_config=rendered,
        )

        assert result["result"] is None
        assert result["changes"] == {}
        assert "would" in result["comment"].lower()
        assert rendered.strip() in result["comment"]
        assert "dry-run output" in result["comment"]
        state_mod.__salt__["file.read"].assert_not_called()
        state_mod.__salt__["cmd.run"].assert_not_called()
        state_mod.__salt__["cmd.which"].assert_called_once_with("kubeadm")
        state_mod.__salt__["cmd.run_all"].assert_called_once_with(
            [
                "kubeadm",
                "init",
                "--config",
                "/etc/kubernetes/kubeadm/init.yaml",
                "--upload-certs",
                "--dry-run",
            ],
            python_shell=False,
            redirect_stderr=False,
        )

    def test_init_test_mode_without_kubeadm_only_renders_config(self, state_mod):
        state_mod.__opts__["test"] = True
        rendered = "apiVersion: kubeadm.k8s.io/v1beta4\nkind: InitConfiguration\n"

        result = state_mod.initialized(
            name="init",
            config="/etc/kubernetes/kubeadm/init.yaml",
            rendered_config=rendered,
        )

        assert result["result"] is None
        assert result["changes"] == {}
        assert "kubeadm is not installed" in result["comment"].lower()
        assert rendered.strip() in result["comment"]
        state_mod.__salt__["cmd.which"].assert_called_once_with("kubeadm")
        state_mod.__salt__["cmd.run"].assert_not_called()
        state_mod.__salt__["cmd.run_all"].assert_not_called()

    def test_init_failure_returns_false(self, state_mod):
        state_mod.__salt__["cmd.run_all"].return_value = {
            "retcode": 1,
            "stdout": "",
            "stderr": "error: port 6443 is in use",
        }

        result = state_mod.initialized(
            name="init", config="/etc/kubernetes/kubeadm/init.yaml"
        )

        assert result["result"] is False
        assert result["changes"] == {}
        assert "port 6443" in result["comment"]

    def test_skip_phases_external_proxy(self, state_mod):
        state_mod.__salt__["cmd.run_all"].return_value = {
            "retcode": 0,
            "stdout": "initialized",
            "stderr": "",
        }

        state_mod.initialized(
            name="init",
            config="/etc/kubernetes/kubeadm/init.yaml",
            skip_phases=["addon/kube-proxy"],
        )

        state_mod.__salt__["cmd.run_all"].assert_called_once_with(
            [
                "kubeadm",
                "init",
                "--config",
                "/etc/kubernetes/kubeadm/init.yaml",
                "--upload-certs",
                "--skip-phases=addon/kube-proxy",
            ],
            python_shell=False,
            redirect_stderr=False,
        )
