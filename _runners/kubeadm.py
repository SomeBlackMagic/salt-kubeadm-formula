"""Read-only helpers used by kubeadm orchestration states."""

from __future__ import annotations

import json
import socket
import ssl
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import salt.client
import salt.client.ssh


def _client(transport):
    if transport == "minion":
        return salt.client.LocalClient()
    if transport == "ssh":
        return salt.client.ssh.SSHClient()
    raise ValueError(f"Unknown transport: {transport}")


def _cmd(target, fun, arg=None, kwarg=None, transport="minion", tgt_type=None):
    client = _client(transport)
    call_kwargs = {"arg": arg, "kwarg": kwarg}
    if tgt_type is not None:
        call_kwargs["tgt_type"] = tgt_type
    return client.cmd(target, fun, **call_kwargs)


def _endpoint_url(endpoint, path):
    endpoint = str(endpoint or "").strip()
    if not endpoint:
        return ""
    if "://" not in endpoint:
        endpoint = f"https://{endpoint}"
    return f"{endpoint.rstrip('/')}/{path.lstrip('/')}"


def _http_check(url, timeout=3):
    if not url:
        return False
    context = ssl._create_unverified_context() if url.startswith("https://") else None
    try:
        request = Request(url, method="GET")
        with urlopen(request, timeout=timeout, context=context) as response:
            return response.status == 200
    except (OSError, ValueError):
        return False


def _truthy_result(result):
    if isinstance(result, dict):
        return any(_truthy_result(value) for value in result.values())
    return result is True


def cluster_exists(nodegroup, transport="minion"):
    """Return true when either the API or local master files indicate a cluster."""
    endpoint = (
        __pillar__.get("kubeadm", {})
        .get("cluster", {})
        .get("controlPlaneEndpoint", "")
    )
    endpoint_exists = _http_check(_endpoint_url(endpoint, "/readyz"))
    admin_conf = _cmd(
        nodegroup,
        "file.file_exists",
        arg=["/etc/kubernetes/admin.conf"],
        transport=transport,
        tgt_type="nodegroup",
    )
    etcd_manifest = _cmd(
        nodegroup,
        "file.file_exists",
        arg=["/etc/kubernetes/manifests/etcd.yaml"],
        transport=transport,
        tgt_type="nodegroup",
    )
    return endpoint_exists or _truthy_result(admin_conf) or _truthy_result(
        etcd_manifest
    )


def endpoint_precheck(endpoint, timeout=3):
    """Check that a control-plane endpoint resolves and accepts TCP connections."""
    value = str(endpoint).strip()
    parsed = urlsplit(value if "://" in value else f"//{value}")
    host = parsed.hostname
    port = parsed.port or 6443
    if not host:
        raise RuntimeError("controlPlaneEndpoint has no hostname")
    try:
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        if not addresses:
            raise RuntimeError(f"Unable to resolve controlPlaneEndpoint: {host}")
        connection = socket.create_connection((host, port), timeout=float(timeout))
        connection.close()
        return True
    except (OSError, TypeError, ValueError) as error:
        raise RuntimeError(
            f"Unable to connect to controlPlaneEndpoint {host}:{port}: {error}"
        ) from error


def _nodegroup_members(nodegroup, transport="minion"):
    result = _cmd(
        nodegroup,
        "test.ping",
        transport=transport,
        tgt_type="nodegroup",
    )
    if not isinstance(result, dict):
        return []
    return sorted(node for node, available in result.items() if available)


def pick_init_node(nodegroup, source_ip=None, transport="minion"):
    """Pick an explicit bootstrap node or the first nodegroup member."""
    if source_ip:
        return source_ip
    members = _nodegroup_members(nodegroup, transport)
    if not members:
        raise RuntimeError(f"No nodes found in nodegroup: {nodegroup}")
    return members[0]


def _single_value(result, node):
    if isinstance(result, dict):
        return result.get(node)
    return result


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
    value = _single_value(result, node)
    if not isinstance(value, dict):
        return False
    try:
        return int(value.get("status", 0)) == 200
    except (TypeError, ValueError):
        return False


def _check_master_health(node, transport):
    admin = _cmd(
        node,
        "file.file_exists",
        arg=["/etc/kubernetes/admin.conf"],
        transport=transport,
    )
    if not _truthy_result(admin):
        return False
    if not _remote_http_ok(node, "https://127.0.0.1:6443/readyz", transport):
        return False
    if not _remote_http_ok(node, "http://127.0.0.1:2381/health", transport):
        return False

    command = _cmd(
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
                "json",
            ]
        ],
        kwarg={"python_shell": False},
        transport=transport,
    )
    command = _single_value(command, node)
    if not isinstance(command, dict) or command.get("retcode") != 0:
        return False
    try:
        node_data = json.loads(command.get("stdout", "{}"))
    except (TypeError, json.JSONDecodeError):
        return False
    conditions = node_data.get("status", {}).get("conditions", [])
    return any(
        item.get("type") == "Ready" and item.get("status") == "True"
        for item in conditions
    )


def pick_source(nodegroup, transport="minion", source_ip=None):
    """Return an explicitly selected or first healthy control-plane node."""
    candidates = [source_ip] if source_ip else _nodegroup_members(
        nodegroup, transport
    )
    for node in candidates:
        if _check_master_health(node, transport):
            return node
    raise RuntimeError("No healthy master is available")
