"""Behavioral rendering tests for kubeadm/binaries.sls."""


BINARIES = {"kubeadm", "kubelet", "kubectl"}


def _binary_files(states):
    return [
        state
        for state in states
        if state["fun"] == "file.managed"
        and state["name"].rsplit("/", 1)[-1] in BINARIES
    ]


class TestBinariesSLS:
    def test_downloads_three_binaries(self, render_sls, minimal_pillar):
        states = render_sls("kubeadm/binaries", minimal_pillar())
        assert {state["name"].rsplit("/", 1)[-1] for state in _binary_files(states)} == BINARIES

    def test_uses_version_arch_and_sha256_url(self, render_sls, minimal_pillar):
        states = render_sls(
            "kubeadm/binaries", minimal_pillar(version="v1.25.16", arch="arm64")
        )
        for state in _binary_files(states):
            binary = state["name"].rsplit("/", 1)[-1]
            expected = (
                "https://dl.k8s.io/release/v1.25.16/bin/linux/arm64/"
                f"{binary}"
            )
            assert state["source"] == expected
            assert state["source_hash"] == f"{expected}.sha256"

    def test_uses_versioned_custom_install_dir(self, render_sls, minimal_pillar):
        pillar = minimal_pillar()
        pillar["kubeadm"]["paths"] = {"install_dir": "/custom/k8s"}
        states = render_sls("kubeadm/binaries", pillar)
        assert {state["name"] for state in _binary_files(states)} == {
            f"/custom/k8s/v1.25.16/{binary}" for binary in BINARIES
        }

    def test_symlinks_are_guarded_and_require_binary(self, render_sls, minimal_pillar):
        states = render_sls("kubeadm/binaries", minimal_pillar())
        symlinks = [state for state in states if state["fun"] == "file.symlink"]
        assert {state["name"].rsplit("/", 1)[-1] for state in symlinks} == BINARIES
        for state in symlinks:
            binary = state["name"].rsplit("/", 1)[-1]
            assert state["onlyif"] == ["true"]
            assert f"/opt/kubernetes/v1.25.16/{binary}" == state["target"]
            assert "require" in state

    def test_does_not_use_cmd_run(self, render_sls, minimal_pillar):
        states = render_sls("kubeadm/binaries", minimal_pillar())
        assert all(state["fun"] != "cmd.run" for state in states)

    def test_existing_symlink_blocks_all_symlink_changes(
        self, render_sls, minimal_pillar
    ):
        states = render_sls(
            "kubeadm/binaries",
            minimal_pillar(),
            linked_paths={"/usr/local/bin/kubeadm"},
        )
        symlinks = [state for state in states if state["fun"] == "file.symlink"]
        assert all(state["onlyif"] == ["false"] for state in symlinks)
