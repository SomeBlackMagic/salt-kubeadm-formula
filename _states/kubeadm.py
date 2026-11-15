"""States for bootstrapping a kubeadm control plane."""

from __future__ import annotations

import time


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


def _http_ok(url):
    try:
        response = __salt__["http.query"](
            url,
            status=True,
            text=True,
            decode=True,
            verify_ssl=False,
        )
    except Exception:  # pylint: disable=broad-except
        return False

    try:
        return int(response.get("status", 0)) == 200
    except (TypeError, ValueError):
        return False


def _check(check):
    if check == "readyz":
        return _http_ok("https://127.0.0.1:6443/readyz")
    if check == "etcd":
        return _http_ok("http://127.0.0.1:2381/health")
    if check == "kubelet":
        return bool(__salt__["service.status"]("kubelet"))
    raise ValueError(f"Unknown kubeadm health check: {check}")


def healthy(name, checks=None, retries=30, interval=10):
    """Check local control-plane health, retrying only in apply mode."""
    checks = checks or ["readyz", "etcd", "kubelet"]
    attempts = 1 if __opts__.get("test") else max(1, int(retries))
    failed = list(checks)

    for attempt in range(attempts):
        failed = []
        for check in checks:
            try:
                passed = _check(check)
            except (KeyError, ValueError):
                passed = False
            if not passed:
                failed.append(check)

        if not failed:
            break
        if attempt + 1 < attempts:
            time.sleep(max(0, float(interval)))

    if __opts__.get("test"):
        status = "healthy" if not failed else f"unhealthy: {', '.join(failed)}"
        return _ret(name, None, f"Current kubeadm health is {status}")
    if failed:
        return _ret(
            name,
            False,
            f"Health checks failed after {attempts} attempts: {', '.join(failed)}",
        )
    return _ret(name, True, "All requested kubeadm health checks passed")
