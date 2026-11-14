"""States for bootstrapping and checking a kubeadm control plane."""

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


def _append_ignored_preflights(command, ignore_preflight_errors):
    if ignore_preflight_errors:
        command.append(
            "--ignore-preflight-errors=" + ",".join(ignore_preflight_errors)
        )


def preflight(
    name,
    config,
    pull_images=True,
    ignore_preflight_errors=None,
):
    """Run kubeadm preflight checks and optionally pre-pull images."""
    preflight_command = [
        "kubeadm",
        "init",
        "phase",
        "preflight",
        "--config",
        config,
    ]
    _append_ignored_preflights(preflight_command, ignore_preflight_errors)
    commands = [preflight_command]
    if pull_images:
        commands.append(
            ["kubeadm", "config", "images", "pull", "--config", config]
        )

    if __opts__.get("test"):
        rendered = " && ".join(" ".join(command) for command in commands)
        return _ret(name, None, f"Would run: {rendered}")

    completed = []
    for command in commands:
        result = _run(command)
        if result.get("retcode", 1) != 0:
            error = result.get("stderr") or result.get("stdout") or "unknown error"
            return _ret(
                name,
                False,
                f"kubeadm preflight failed: {error}",
                {"completed": completed},
            )
        completed.append(" ".join(command))

    return _ret(
        name,
        True,
        "kubeadm preflight checks passed",
        {"completed": completed},
    )


def initialized(
    name,
    config,
    patches=None,
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
    # When --config is used, kubeadm requires the patches directory in
    # InitConfiguration.patches.directory and rejects the --patches flag.
    if skip_phases:
        command.append(f"--skip-phases={','.join(skip_phases)}")
    _append_ignored_preflights(command, ignore_preflight_errors)

    if __opts__.get("test"):
        if rendered_config is None:
            try:
                rendered_config = __salt__["file.read"](config)
            except (OSError, KeyError) as error:
                rendered_config = f"Unable to read {config}: {error}"
        return _ret(
            name,
            None,
            "Would initialize Kubernetes with the rendered config:\n"
            f"{rendered_config}",
            {"command": " ".join(command)},
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
