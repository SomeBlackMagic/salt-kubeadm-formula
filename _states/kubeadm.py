"""States for bootstrapping a kubeadm control plane."""

from __future__ import annotations


__virtualname__ = "kubeadm"


def __virtual__():
    return __virtualname__


def _ret(name, result, comment, changes=None):
    return {
        "name": name,
        "result": result,
        "changes": changes or {},
        "comment": comment,
    }


def _run(command):
    return __salt__["cmd.run_all"](
        command,
        python_shell=False,
        redirect_stderr=False,
    )


def _append_csv_flag(command, flag, values):
    if values:
        command.append(f"{flag}={','.join(values)}")


def _rendered_config(config, rendered_config):
    if rendered_config is not None:
        return rendered_config
    try:
        return __salt__["file.read"](config)
    except (OSError, KeyError) as error:
        return f"Unable to read {config}: {error}"


def initialized(
    name,
    config,
    upload_certs=True,
    skip_phases=None,
    ignore_preflight_errors=None,
    rendered_config=None,
):
    """Initialize a control plane unless ``admin.conf`` already exists."""
    if __salt__["file.file_exists"]("/etc/kubernetes/admin.conf"):
        return _ret(name, True, "Kubernetes is already initialized")

    command = ["kubeadm", "init", "--config", config]
    if upload_certs:
        command.append("--upload-certs")
    _append_csv_flag(command, "--skip-phases", skip_phases)
    _append_csv_flag(
        command,
        "--ignore-preflight-errors",
        ignore_preflight_errors,
    )

    if __opts__.get("test"):
        config_text = _rendered_config(config, rendered_config)
        command_text = " ".join(command)
        if not __salt__["cmd.which"]("kubeadm"):
            return _ret(
                name,
                None,
                "Would initialize Kubernetes, but kubeadm is not installed.\n"
                f"Command: {command_text}\n"
                "Rendered config:\n"
                f"{config_text}",
            )

        dry_run_command = [*command, "--dry-run"]
        dry_run = _run(dry_run_command)
        output = dry_run.get("stdout") or dry_run.get("stderr") or "No output"
        return _ret(
            name,
            None,
            "Would initialize Kubernetes with the rendered config.\n"
            f"Command: {' '.join(dry_run_command)}\n"
            f"Dry-run retcode: {dry_run.get('retcode', 1)}\n"
            f"Dry-run output:\n{output}\n"
            f"Rendered config:\n{config_text}",
        )

    result = _run(command)
    if result.get("retcode", 1) != 0:
        error = result.get("stderr") or result.get("stdout") or "unknown error"
        return _ret(name, False, f"kubeadm init failed: {error}")

    return _ret(
        name,
        True,
        result.get("stdout") or "Kubernetes control plane initialized",
        {"initialized": "/etc/kubernetes/admin.conf"},
    )
