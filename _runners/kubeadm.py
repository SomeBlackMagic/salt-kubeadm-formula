"""Read-only orchestration helpers for kubeadm clusters."""

from __future__ import annotations

import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request

import salt.client
import salt.client.ssh

try:
    from salt.client.ssh.client import SSHClient
except ImportError:  # pragma: no cover - compatibility with older Salt/tests
    SSHClient = salt.client.ssh.SSHClient


def _client(transport):
    """Return a Salt client for the requested node transport."""
    if transport == "minion":
        return salt.client.LocalClient()
    if transport == "ssh":
        return SSHClient()
    raise ValueError(f"Unknown transport: {transport}")


def _cmd(
    target,
    fun,
    arg=None,
    kwarg=None,
    transport="minion",
    tgt_type=None,
):
    """Run one execution function through minion or salt-ssh."""
    client = _client(transport)
    call_kwargs = {"arg": arg, "kwarg": kwarg}
    if tgt_type is not None:
        call_kwargs["tgt_type"] = tgt_type
    return client.cmd(target, fun, **call_kwargs)


def _value_truthy(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, dict):
        if value.get("retcode", 0) not in (None, 0):
            return False
        if "return" in value:
            return _value_truthy(value["return"])
        if "stdout" in value:
            return _value_truthy(value["stdout"])
        if "status" in value:
            try:
                return int(value["status"]) == 200
            except (TypeError, ValueError):
                return False
    if isinstance(value, str):
        return value.strip().lower() in {"true", "ok", "healthy"}
    return bool(value)


def _truthy_result(result):
    """Normalize LocalClient and SSHClient return envelopes."""
    if not isinstance(result, dict) or not result:
        return False
    return any(_value_truthy(value) for value in result.values())


def _endpoint_url(endpoint, path="/readyz"):
    endpoint = str(endpoint or "").strip()
    if not endpoint:
        raise RuntimeError("kubeadm:cluster:controlPlaneEndpoint is required")
    parsed = urllib.parse.urlsplit(
        endpoint if "://" in endpoint else f"https://{endpoint}"
    )
    if not parsed.hostname:
        raise RuntimeError(f"Invalid controlPlaneEndpoint: {endpoint}")
    port = parsed.port or 6443
    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    normalized_path = "/" + str(path).lstrip("/")
    return f"https://{host}:{port}{normalized_path}"


def _http_check(url, timeout=3.0):
    """Return whether an HTTPS health endpoint responds with status 200."""
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(
            url,
            timeout=float(timeout),
            context=context,
        ) as response:
            return int(response.status) == 200
    except (OSError, ValueError, urllib.error.URLError):
        return False


def _nodegroup_members(nodegroup, transport="minion"):
    """Return sorted responding members of an explicit Salt nodegroup."""
    result = _cmd(
        nodegroup,
        "test.ping",
        transport=transport,
        tgt_type="nodegroup",
    )
    if not isinstance(result, dict):
        return []
    return sorted(
        node for node, value in result.items() if _value_truthy(value)
    )


def cluster_exists(
    nodegroup,
    transport="minion",
    endpoint=None,
    source_ip=None,
):
    """Detect a cluster through both its LB and node-local artifacts."""
    if endpoint is None:
        endpoint = __pillar__.get("kubeadm", {}).get("cluster", {}).get(
            "controlPlaneEndpoint", ""
        )
    endpoint_ready = _http_check(_endpoint_url(endpoint, "/readyz"))
    target = source_ip or nodegroup
    target_type = None if source_ip else "nodegroup"
    artifact_results = []
    for path in (
        "/etc/kubernetes/admin.conf",
        "/etc/kubernetes/manifests/etcd.yaml",
    ):
        artifact_results.append(
            _cmd(
                target,
                "file.file_exists",
                arg=[path],
                transport=transport,
                tgt_type=target_type,
            )
        )
    return endpoint_ready or any(
        _truthy_result(result) for result in artifact_results
    )


def _endpoint_host_port(endpoint):
    parsed = urllib.parse.urlsplit(
        endpoint if "://" in str(endpoint) else f"//{endpoint}"
    )
    if not parsed.hostname:
        raise RuntimeError(f"Invalid controlPlaneEndpoint: {endpoint}")
    return parsed.hostname, parsed.port or 6443


def endpoint_precheck(endpoint, timeout=3.0):
    """Verify that the external API endpoint resolves and accepts TCP."""
    host, port = _endpoint_host_port(endpoint)
    try:
        socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as error:
        raise RuntimeError(f"Unable to resolve {host}: {error}") from error
    try:
        connection = socket.create_connection(
            (host, port), timeout=float(timeout)
        )
        connection.close()
    except OSError as error:
        raise RuntimeError(
            f"Unable to connect to {host}:{port}: {error}"
        ) from error
    return True


def pick_init_node(
    nodegroup,
    source_ip=None,
    transport="minion",
):
    """Choose the explicit source override or first responding master."""
    if source_ip:
        return source_ip
    members = _nodegroup_members(nodegroup, transport)
    if not members:
        raise RuntimeError(f"No nodes found in nodegroup {nodegroup}")
    return members[0]


def _single_result(result, node):
    if not isinstance(result, dict):
        return result
    return result.get(node)


def _remote_http_ok(node, url, transport):
    result = _cmd(
        node,
        "http.query",
        arg=[url],
        kwarg={
            "status": True,
            "text": True,
            "decode": True,
            "verify_ssl": False,
        },
        transport=transport,
    )
    value = _single_result(result, node)
    if isinstance(value, dict) and "return" in value:
        value = value["return"]
    return isinstance(value, dict) and str(value.get("status")) == "200"


def _check_master_health(node, transport="minion"):
    admin_conf = _cmd(
        node,
        "file.file_exists",
        arg=["/etc/kubernetes/admin.conf"],
        transport=transport,
    )
    if not _truthy_result(admin_conf):
        return False
    if not _remote_http_ok(
        node, "https://127.0.0.1:6443/readyz", transport
    ):
        return False
    if not _remote_http_ok(node, "http://127.0.0.1:2381/health", transport):
        return False

    ready = _cmd(
        node,
        "cmd.run_all",
        arg=[
            [
                "kubectl",
                "--kubeconfig",
                "/etc/kubernetes/admin.conf",
                "get",
                "node",
                node,
                "-o",
                "jsonpath={.status.conditions[?(@.type=='Ready')].status}",
            ]
        ],
        kwarg={"python_shell": False},
        transport=transport,
    )
    ready_value = _single_result(ready, node)
    if isinstance(ready_value, dict) and "return" in ready_value:
        ready_value = ready_value["return"]
    if not isinstance(ready_value, dict):
        return False
    if ready_value.get("retcode") != 0:
        return False
    return str(ready_value.get("stdout", "")).strip() == "True"


def pick_source(
    nodegroup,
    transport="minion",
    source_ip=None,
):
    """Pick the first healthy control-plane node."""
    candidates = (
        [source_ip]
        if source_ip
        else _nodegroup_members(nodegroup, transport)
    )
    for node in candidates:
        if _check_master_health(node, transport):
            return node
    raise RuntimeError("No healthy master is available")
