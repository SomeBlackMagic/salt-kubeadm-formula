# salt-kubeadm-formula — Design Spec

Nov 10, 2026 · @Andrii

## 1. Overview

The formula deploys and operates kubeadm-based Kubernetes clusters with Salt, covering day-1 (bootstrap, join) and day-2 (reconfigure, node removal, certificates, upgrade, addons) through one model that works over both salt-minion and salt-ssh.

### Goals

- Install any Kubernetes version on any distro from upstream binaries, without distro packages.
- Bootstrap a cluster and join control-plane and worker nodes safely, one control-plane node at a time.
- Change kubelet and control-plane settings on a live cluster, cluster-wide or on a single node.
- Keep etcd quorum safe in every scenario: join, remove, replace, reconfigure, certs, upgrade.
- Manage kube-proxy and CoreDNS configuration from pillar.
- Make `test=True` safe everywhere and informative (real diffs, not "would run").
- Show progress of long operations in real time.

### Non-goals

- Container runtime and node prerequisites (kernel modules, sysctl, swap, CNI plugins, crictl): owned by the containerd formula (SomeBlackMagic/salt-containerd-formula).
- CNI network plugin (Calico, Cilium): installed via GitOps.
- API load balancer: external, not managed by the formula.
- CA rotation (10-year CA): monitored only.
- Version upgrade policy (when and to which version): out of scope; the formula provides the mechanism.

### Baseline and dependencies

| Item | Value |
| --- | --- |
| Minimum Kubernetes version | 1.25 |
| Container runtime | containerd, via the containerd formula (SystemdCgroup = true) |
| CNI plugins, crictl | installed by the containerd formula |
| controlPlaneEndpoint | external load balancer, backend pool = all masters, HTTPS health check on `/readyz` |
| `admin.conf` | stays on masters only; never in pillar, mine or events |
| Transport | salt-minion and salt-ssh, both supported |
| conntrack, socat | open question, see section 9 |

## 2. Architecture

The formula has two layers: idempotent node states that only touch the local node, and orchestrate scenarios that own ordering, cluster-wide actions and transport.

&#91;embedded content: formula architecture · Salt master, masters, workers, LB\]

Orchestrate and runners on the Salt master drive both node groups over either transport; cluster-level commands always execute on a master, and joins reach the cluster through the external LB.

### Layers

1. **Node states** (`kubeadm.*`, applied by highstate or `state.apply`): binaries, kubelet unit, config, patches, reconfigure, certs. They read data only from pillar. They never use mine, never call `event.send`, never know the transport.
2. **Orchestrate scenarios** (`orch.kubeadm.*`, run explicitly with `salt-run state.orchestrate`): bootstrap, join-controlplane, join-worker, remove-node, reconfigure-kubelet, reconfigure-controlplane, certs, upgrade, addons. They pick nodes, enforce order, run health checks between nodes and emit progress events.
3. **Custom Python code** (`_modules`, `_runners`, `_states`): pure helpers, cluster-level operations, and kubeadm operations with real `test=True` support.

### Transport-agnostic rule

- The same SLS files run over salt-minion and salt-ssh.
- Orchestrate gets `kubeadm:orchestrate:transport` (`minion` | `ssh`) and sets `ssh: True` on every `salt.state` / `salt.function` step through Jinja.
- Runners use `LocalClient` for minions and `SSHClient` for salt-ssh, selected by the same parameter.
- Data between steps (tokens, keys) is never passed through Salt Mine. Runners fetch it from a healthy master and pass it to the next step via `pillar:`.
- Events are sent by orchestrate (`salt.runner event.send`), not by nodes: salt-ssh nodes have no event bus.
- Custom modules are synced into the salt-ssh thin, so behaviour is identical.

### Targeting

- Nodegroups defined as explicit host lists in the master config, e.g. `k8s-masters: L@master-1,master-2,master-3`, `k8s-workers: L@worker-1,worker-2`.
- For salt-ssh the same names must match roster IDs.
- Grain-based nodegroups are avoided: under salt-ssh grains come from cache.
- A node's role is set explicitly in pillar (`kubeadm:node:role`), not derived from nodegroup membership.
- Nodes being removed are listed in `kubeadm:orchestrate:decommissioned` so default join targets skip them.

### Repository layout

```
kubeadm/
  map.jinja              # merges defaults -> cluster -> role -> node, runs denylist checks
  defaults.yaml          # defaults, denylists, named-list keys, version table
  macros.jinja
  init.sls               # includes node-level states
  binaries.sls
  kubelet.sls            # systemd unit, drop-in, /etc/default/kubelet
  config.sls             # kubeadm config + patches
  reconfigure.sls
  certs/
    init.sls             # renew script + systemd timer
    check.sls
  addons.sls             # kube-proxy, CoreDNS (cluster-level, one master)
  init_cluster.sls       # used by orch bootstrap only
  join.sls               # used by orch join only
  reset.sls              # guarded by confirm_reset
  files/                 # unit templates, renew script
_modules/kubeadm_util.py
_runners/kubeadm.py
_states/kubeadm.py
orch/kubeadm/
  bootstrap.sls
  join-controlplane.sls
  join-controlplane-node.sls
  join-worker.sls
  join-worker-batch.sls
  remove-node.sls
  reconfigure-kubelet.sls
  reconfigure-controlplane.sls
  certs.sls
  upgrade.sls
  upgrade-node.sls
  addons.sls
test/
```

## 3. Pillar model

Pillar has three data layers merged explicitly in `map.jinja` (`defaults.yaml` → `cluster` → `roles[role]` → `node`) plus a separate block for orchestrate parameters; explicit merging keeps the result deterministic for both transports.

### Layers

| Layer | Pillar key | Scope | Maps to |
| --- | --- | --- | --- |
| Cluster | `kubeadm:cluster`, `kubeadm:kubelet` | all nodes | ClusterConfiguration (`kubeadm-config` CM), KubeletConfiguration (`kubelet-config` CM) |
| Role | `kubeadm:roles:<control-plane\|worker>` | per role | flags, taints, patches |
| Node | `kubeadm:node` | one minion | role, advertise address, flags, labels, taints, patches |
| Addons | `kubeadm:addons` | cluster | kube-proxy ConfigMap + DaemonSet, CoreDNS Corefile + Deployment |
| Orchestrate | `kubeadm:orchestrate` | Salt master only | transport, nodegroups, source IP, health-check timing, decommissioned list |

### Example

```yaml
# pillar/kubeadm/cluster.sls
kubeadm:
  version: v1.25.16
  arch: amd64
  download:
    base_url: https://dl.k8s.io/release
  paths:
    install_dir: /opt/kubernetes      # versioned: /opt/kubernetes/v1.25.16/
    bin_dir: /usr/local/bin           # symlinks
    patches_dir: /etc/kubernetes/patches
  cluster:                            # -> ClusterConfiguration
    clusterName: prod
    controlPlaneEndpoint: k8s-api.example.local:6443
    networking:
      serviceSubnet: 10.96.0.0/12
      podSubnet: 10.244.0.0/16
      dnsDomain: cluster.local
    apiServer:
      certSANs: [k8s-api.example.local]
      extraArgs: {}                   # always a dict in pillar
    controllerManager: {extraArgs: {}}
    scheduler: {extraArgs: {}}
    etcd:
      local: {extraArgs: {}}
  kubelet:                            # -> KubeletConfiguration
    cgroupDriver: systemd
    clusterDNS: [10.96.0.10]
    clusterDomain: cluster.local
```

```yaml
# pillar/kubeadm/roles.sls
kubeadm:
  roles:
    control-plane:
      taints:
        node-role.kubernetes.io/control-plane: {effect: NoSchedule}
    worker:
      patches:
        kubeletconfiguration:
          maxPods: 200
```

```yaml
# pillar/kubeadm/nodes/master-2.sls
kubeadm:
  node:
    role: control-plane
    advertise_address: 10.0.0.12
    flags:                            # -> /etc/default/kubelet (KUBELET_EXTRA_ARGS)
      node-ip: 10.0.0.12
    labels:
      topology.example.io/rack: r2
    patches:                          # -> /etc/kubernetes/patches/<target>+strategic.yaml
      kubeletconfiguration:
        systemReserved: {cpu: 500m, memory: 1Gi}
        imageGCHighThresholdPercent: 80
      kube-apiserver:
        spec:
          containers:
            kube-apiserver:           # named list stored as dict, key = name
              resources:
                requests: {cpu: "1", memory: 2Gi}
              env:
                GOMAXPROCS: {value: "4"}
```

```yaml
# pillar/kubeadm/orchestrate.sls (Salt master only)
kubeadm:
  orchestrate:
    transport: minion                 # minion | ssh
    nodegroups:
      masters: k8s-masters
      workers: k8s-workers
    source_ip: ~                      # optional override for join/ops source
    decommissioned: []
    health:
      retries: 30
      interval: 10
```

Top file uses explicit host lists (`L@`), matching the nodegroups; `N@` in pillar top under salt-ssh must be verified before use.

### Named lists as dicts

- Kubernetes lists keyed by `name` (`containers`, `initContainers`, `env`, `volumes`, `volumeMounts`, `ports`) are stored in pillar as dicts keyed by name.
- Layers then merge recursively: a node layer can change one field without replacing the whole list.
- `kubeadm_util.to_k8s()` converts them back to lists when rendering patch files; the key set lives in `defaults.yaml`.
- Strategic merge patches merge `containers` and `env` by `name`, so the manifest change is also field-level.
- `extraArgs` is always a dict in pillar; `kubeadm_util.extra_args()` renders a map for `v1beta3` (1.25) and a `{name, value}` list for `v1beta4` (1.31+).

### Labels and taints

- Stored as dicts in role and node layers.
- Taints are registered by kubelet at join (`register-with-taints`) so nothing schedules early.
- Labels are applied from a master via `kubectl label` by `kubeadm.node_meta`: NodeRestriction forbids kubelet self-labeling with `node-role.kubernetes.io/` and `node-restriction.kubernetes.io/`, and kubelet sets labels only at first registration.
- The formula records the labels it owns in node annotation `kubeadm.salt/managed-labels` and removes only those.

### Denylists

`map.jinja` fails rendering with `raise` when a forbidden path appears in a role or node layer, before any change on the node.

| Area | Forbidden per node / role | Reason |
| --- | --- | --- |
| Kubelet | `clusterDNS`, `clusterDomain` | must match cluster-wide |
| Kubelet | `cgroupDriver` | must match containerd |
| Kubelet | `rootDir`, `staticPodPath` | require drain or node rebuild |
| Control plane | `serviceSubnet`, `podSubnet`, `controlPlaneEndpoint` | cluster-wide |
| Control plane | feature-gates, admission plugins, authorization modes, OIDC, service account issuer | inconsistent behaviour across masters |
| Control plane | encryption config, audit policy | masters must read each other's data |
| etcd | membership flags (name, initial-cluster, peer URLs), heartbeat and election timeouts | owned by kubeadm, must be uniform |
| etcd target | whole `etcd` patch target in reconfigure (v1) | see section 9 |

### Kubelet flags outside KubeletConfiguration

`node-ip`, `node-labels`, `register-with-taints`, `hostname-override` and, in 1.25, `container-runtime-endpoint` and `pod-infra-container-image` live in `/var/lib/kubelet/kubeadm-flags.env`, written by kubeadm only at init/join. The formula manages per-node flags in `/etc/default/kubelet` (`KUBELET_EXTRA_ARGS`), referenced by its own kubelet unit.

### Addons pillar

```yaml
kubeadm:
  addons:
    kube_proxy:
      managed_by: kubeadm             # kubeadm | external
      config:                         # -> ConfigMap kube-proxy / config.conf
        mode: ipvs
      daemonset:                      # patch, named lists as dicts
        spec:
          template:
            spec:
              containers:
                kube-proxy:
                  resources: {requests: {cpu: 100m, memory: 64Mi}}
    coredns:
      managed_by: kubeadm
      corefile: |                     # raw Corefile text
        .:53 { ... reload }
      deployment:
        spec:
          replicas: 3
```

### Secrets

- Bootstrap tokens and certificate keys are never stored in pillar: generated at runtime, valid for one orchestrate run.
- Encryption-config keys, if used, go through the GPG renderer.

## 4. Custom code

All non-trivial logic lives in three Python files; Jinja only wires data, which keeps the logic testable with pytest and identical over salt-ssh.

### `_modules/kubeadm_util.py` — pure helpers

No side effects; unit-tested without a Salt environment.

| Function | Purpose |
| --- | --- |
| `to_k8s(obj)` | convert named-list dicts back to Kubernetes lists, recursively |
| `extra_args(args, api_version)` | dict → map (`v1beta3`) or `{name, value}` list (`v1beta4`) |
| `merge_layers(defaults, cluster, role, node)` | recursive merge used by `map.jinja` |
| `check_denylist(layer, denylist)` | return violations; `map.jinja` raises if any |

### `_runners/kubeadm.py` — cluster-level operations

Runners execute commands on masters through `LocalClient` (minion) or `SSHClient` (ssh) and return only command results, never `admin.conf` contents.

Read-only (allowed at render time):

| Function | Purpose |
| --- | --- |
| `cluster_exists(nodegroup, transport)` | `/readyz` via endpoint AND `admin.conf` / etcd manifest present on any master |
| `pick_init_node(nodegroup, source_ip)` | init node for bootstrap |
| `pick_source(nodegroup, transport, source_ip=None)` | first healthy master: `/readyz`, etcd health, node Ready, `admin.conf` not expired |
| `cp_members(source)` | etcd members + control-plane nodes |
| `nodes(source)` / `pending_nodes(nodegroup)` | nodes in cluster / nodegroup members not yet joined, minus decommissioned |
| `etcd_precheck(source, joining)` | all members started and healthy, no stale member with same name or peer URL |
| `etcd_postcheck(source, joined)` | new member started, all healthy |
| `diff(layer)` | pillar vs `kubeadm-config` / `kubelet-config` / addon ConfigMaps |
| `cert_expiry(nodegroup)` | expiry table for all nodes |
| `versions(nodegroup)` | kubelet version per Node, control-plane image versions |

Mutating (never at render time; check `__opts__['test']` first and return `result: None` with a description in test mode):

| Function | Purpose |
| --- | --- |
| `join_node(node, role, source)` | issue fresh token (TTL 15m) + CA hash, plus `upload-certs` key for control plane, then apply `kubeadm.join` on the node |
| `etcd_member_remove(source, node)` | remove a member through etcdctl in the etcd pod |
| `move_leader(source, target)` | `etcdctl move-leader` away from a member before removal |
| `upload_config(source, kind)` | `kubeadm init phase upload-config kubeadm\|kubelet` |
| `node_meta(source, node)` | reconcile labels and taints, tracked via annotation |
| `join_rollback(source, node)` | remove unstarted etcd member if present, `kubeadm reset -f` on the node |
| `etcd_snapshot(source)` | `etcdctl snapshot save` before upgrade |

### `_states/kubeadm.py` — kubeadm operations with real test mode

Replaces `cmd.run` for every kubeadm call so `test=True` returns real diffs.

| State | Apply | `test=True` |
| --- | --- | --- |
| `kubeadm.initialized` | `kubeadm init` | whether init would run, rendered config; optionally `init --dry-run` |
| `kubeadm.joined` | `kubeadm join` | whether join would run, with which config and source |
| `kubeadm.kubelet_config_applied` | `upgrade node phase kubelet-config --patches` | fetch `kubelet-config` CM, apply node patch in Python, diff vs `/var/lib/kubelet/config.yaml` |
| `kubeadm.control_plane_applied` | `init phase control-plane all --config node.yaml --patches` | render manifests into a temp dir, diff vs `/etc/kubernetes/manifests` |
| `kubeadm.certs_renewed` | renew script | expiry table, what would renew, restart order |
| `kubeadm.upgraded` | `upgrade apply` / `upgrade node` | `upgrade plan`, `--dry-run` output |
| `kubeadm.addon_applied` | ConfigMap + workload apply, rollout | ConfigMap diff, `kubectl diff` (server-side dry-run) |
| `kubeadm.healthy` | checks with retry | single check of current state, no waiting |

Rules:

- `unless` / `onlyif` must be side-effect-free (`test -f`, reads only): they run in test mode too.
- Files carrying tokens use mode `0600` and `show_changes: False`, and are deleted right after use.

## 5. Node states

Node states are safe to apply by highstate at any time: none of them initializes, joins, upgrades or switches the active Kubernetes version on an existing node.

### `kubeadm.binaries`

- Downloads `kubeadm`, `kubelet`, `kubectl` from `dl.k8s.io` for `kubeadm:version` and `kubeadm:arch`, verifies sha256.
- Installs into `/opt/kubernetes/vX.Y.Z/`; keeps the two latest versions.
- Creates symlinks in `/usr/local/bin` ONLY on a new node with no symlinks yet. On existing nodes, changing `kubeadm:version` only pre-stages binaries; switching symlinks is done exclusively by `orch.kubeadm.upgrade`.

### `kubeadm.kubelet`

- Formula-owned systemd unit `kubelet.service` and drop-in `10-kubeadm.conf` (EnvironmentFile for `kubeadm-flags.env` and `/etc/default/kubelet`, `--config=/var/lib/kubelet/config.yaml`, bootstrap/kubeconfig flags).
- `/etc/default/kubelet` rendered from merged `flags`.
- Service enabled, not forced running before init/join: kubeadm starts it.

### `kubeadm.config`

- Renders kubeadm config documents into `/etc/kubernetes/kubeadm/` (InitConfiguration / JoinConfiguration, ClusterConfiguration, KubeletConfiguration, KubeProxyConfiguration as needed).
- Renders patches into `/etc/kubernetes/patches/<target>+strategic.yaml`, mode `0600`, via `kubeadm_util.to_k8s`. Targets: `kubeletconfiguration`, `kube-apiserver`, `kube-controller-manager`, `kube-scheduler`, `etcd`.
- `file.directory` with `clean: true`: a patch removed from pillar is removed from the node.

### `kubeadm.reconfigure` (single node, also used by orchestrate)

Run directly for one node: `salt master-2 state.apply kubeadm.reconfigure`.

1. Render patches and `/etc/default/kubelet` from merged pillar.
2. Backup `/var/lib/kubelet/config.yaml` and `/etc/kubernetes/manifests` to `/etc/kubernetes/salt-backup/<timestamp>/`; keep the last N.
3. Kubelet: on change of the `kubeletconfiguration` patch → `kubeadm.kubelet_config_applied`; restart kubelet on change of the patch or flags (`watch`).
4. Control plane (masters only): on change of component patches or cluster layer → fetch ClusterConfiguration from `kubeadm-config` (or take it from orchestrate pillar), apply node overlay → `node.yaml` → `kubeadm.control_plane_applied`. Kubelet restarts static pods itself.
5. Health checks with retry: kubelet active; node not worse than before (NotReady accepted if it was NotReady, e.g. no CNI); mirror pod hash matches manifest; local `/readyz`; etcd healthy.
6. `onfail`: restore manifests and kubelet config from backup, restart kubelet, re-run checks; the state still fails so orchestrate stops.

Kubelet configuration mechanism by version, same pillar format:

| Version | Mechanism | Apply |
| --- | --- | --- |
| 1.25 – 1.29 | `kubeletconfiguration` patch | `kubeadm upgrade node phase kubelet-config --patches` + kubelet restart |
| 1.30+ | kubelet drop-in dir (`--config-dir`, beta in 1.30) | write drop-in file + kubelet restart; kubeadm never overwrites it |

Control-plane per-node options:

- **Patches**: official kubeadm mechanism, survive `kubeadm upgrade`; preferred for small per-node changes (resources, log verbosity, env such as GOMAXPROCS).
- **Overlay**: full ClusterConfiguration from the ConfigMap with node overrides merged on top. Lost on `kubeadm upgrade`, so the upgrade scenario re-runs `kubeadm.reconfigure` on nodes that have an overlay.
- Safe per master: resources and limits, log verbosity, advertise/bind address, env.
- `controller-manager` and `scheduler` use leader election: differing flags make behaviour depend on the current leader; avoid.
- `certSANs` changes need certificate re-issue (see reconfigure-controlplane), not just a manifest change.

### `kubeadm.certs` and `kubeadm.certs.check`

- Installs the renew script `/usr/local/bin/kubeadm-certs-renew` and `kubeadm-certs-renew.service` + `.timer` on masters, enabled by `kubeadm:certs:auto_renew` (default false).
- Timer staggered by master index in the nodegroup (Kubespray-style), e.g. first Monday of the month at 03:`<index>`0.
- `kubeadm.certs.check`: runs the script in `--check` mode; meant for Salt scheduler on masters; orchestrate emits days-left as an event for alerting.
- Details of the script: section 6.6.

### `kubeadm.addons` (cluster-level, one master)

Targeted by `orch.kubeadm.addons` at one healthy master chosen by `pick_source`, never at all masters. Details: section 6.8.

### `kubeadm.reset`

`kubeadm reset -f`, cleanup of `/etc/kubernetes`, `/var/lib/etcd`, `/etc/cni/net.d`, iptables/ipvs rules. Runs only with `pillar='{"kubeadm": {"confirm_reset": true}}'`, except inside the join rollback for the node being joined.

## 6. Orchestrate scenarios

Every scenario is run explicitly, processes control-plane nodes strictly one at a time with health checks in between, and stops at the first failed node.

Common parameters live in `kubeadm:orchestrate` (section 3). Per-node work runs as a nested orchestrate rendered right before it starts, so fresh tokens and current cluster state are used; nested orchestrates always receive `test` explicitly.

### 6.1 bootstrap

Creates the first master only; other nodes come from join scenarios. Flag `full: true` chains join-controlplane and join-worker after init (default false).

1. **Guard**: `cluster_exists` checks `/readyz` on controlPlaneEndpoint AND `admin.conf` / etcd manifest on every master. Any sign of a cluster → fail with "cluster exists, use join-controlplane". Checking only through the LB could give a false "no cluster" when the LB is down.
2. **Endpoint precheck**: controlPlaneEndpoint resolves and port 6443 is reachable; otherwise fail before init.
3. **Init node**: `source_ip` or the first host of the masters nodegroup.
4. **containerd** formula applied from orchestrate.
5. **Node states**: binaries, kubelet, config (InitConfiguration + ClusterConfiguration + KubeletConfiguration + KubeProxyConfiguration), patches.
6. **Preflight**: `kubeadm init phase preflight --config …`, `kubeadm config images pull`.
7. **Init**: `kubeadm init --config … --upload-certs --patches /etc/kubernetes/patches`, guarded by absence of `admin.conf`. With `managed_by: external` add `--skip-phases=addon/kube-proxy` and/or `addon/coredns`.
8. **Health**: `/readyz`, etcd health. Node Ready is NOT required (no CNI yet).
9. **Event** `kubeadm/bootstrap/done/<node>`: hook point for GitOps bootstrap via reactor running on a master.

LB requirement (README): the backend pool contains all future masters with an HTTPS `/readyz` health check before init, because kubeadm talks to the cluster through controlPlaneEndpoint right after the apiserver starts.

Failed init leaves a half state: recover with `kubeadm.reset` (requires `confirm_reset`).

### 6.2 join-controlplane

Outer orchestrate selects targets (`targets` param, default = masters nodegroup members not in `cp_members`, minus decommissioned) and calls `join-controlplane-node` for each, sequentially.

Per master:

1. **etcd precheck**: all existing members started and healthy; any unhealthy member → refuse. A member with the same name or peer URL already present → fail and point to remove-node.
2. **containerd + node states** on the target.
3. **Preflight**: `kubeadm join phase preflight`.
4. **Join** via runner `join_node`: fresh token (TTL 15m), CA hash and certificate key from a healthy source (`upload-certs` key lives 2h). Rendered into `/etc/kubernetes/kubeadm/join.yaml` (0600, no diff output), `kubeadm join --config … --patches …`, guarded by absence of `kubelet.conf`; file deleted after.
5. **Postcheck**: new member started, all members healthy, local `/readyz`, static pods running.
6. **Event**, then next master.

Rollback (`onfail` of join or postcheck): kubeadm adds the etcd member before etcd starts on the new node; a half-joined node leaves an unstarted voting member. `join_rollback` removes that member from a healthy master, then runs `kubeadm reset -f` on the target, returning the cluster to its prior state.

Quorum notes:

- No etcd learner mode in 1.25 (alpha in 1.27): a new member votes immediately.
- 1 → 2 masters is the riskiest step: quorum becomes 2. Recommended path: join two masters back-to-back (1 → 3).
- If targets lead to an even number of masters, a warning is emitted.

Cleanup: after the last master, delete Secret `kubeadm-certs` in kube-system instead of waiting for its TTL.

Join source selection: `source_ip` if set; otherwise the first healthy master. Join itself always goes through controlPlaneEndpoint, so one master in maintenance does not block joins.

### 6.3 join-worker

1. **Targets**: `targets` param or workers nodegroup members without a Node object, minus decommissioned.
2. **Precheck**: apiserver healthy via source. Existing Node with the same name → fail and point to remove-node; `replace: true` deletes the old Node only if it is NotReady.
3. **Batches**: chunked in Jinja (`targets | batch(n)`), identical for both transports (salt-ssh has no `--batch`; `ssh_max_procs` limits parallelism inside a batch). One nested orchestrate per batch, fresh token per batch.
4. **Node states**, then join as in 6.2 without certificate key.
5. **Postcheck**: kubelet active, `kubelet.conf` present, Node registered. Ready only with `wait_ready: true` (when CNI already exists).
6. **Labels and taints** via `node_meta` from a master.
7. **Event** per batch.

Failure policy: `fail_fast: true` by default (`require` between batches); `max_fail: N` lets a runner count failures and continue. Failed worker: `kubeadm reset -f` + delete Node if registered; `keep_failed: true` leaves it for debugging.

### 6.4 remove-node

Role is detected from reality (etcd membership, control-plane manifests), not pillar. Parameters: `target`, `drain: true`, `drain_timeout: 10m`, `purge: false`, `node_dead: false`, `force_drain: false`.

Worker:

1. Precheck: healthy source; Node absent → succeed (idempotent).
2. cordon + `kubectl drain --ignore-daemonsets --delete-emptydir-data --timeout` from a master; PDBs respected. Blocked drain → fail with the blocking pods in the event; `--disable-eviction` only with `force_drain`.
3. On the node if reachable: `kubeadm reset -f`, stop/disable kubelet, clean `/etc/cni/net.d` and iptables/ipvs rules; `purge` removes binaries and unit.
4. `kubectl delete node`.
5. Event.

Master:

1. **Quorum precheck** from a different healthy master: remaining healthy members must hold quorum for the new size. Removing the last master is refused. Even result → warning.
2. **Move leader** if the target is the etcd leader.
3. cordon + drain.
4. **Remove etcd member** explicitly via `etcd_member_remove`; then on the node `kubeadm reset -f --skip-phases remove-etcd-member`, so removal happens only after our checks.
5. Cleanup as for a worker, plus `/var/lib/etcd`.
6. `kubectl delete node`.
7. **Postcheck**: member gone, remaining healthy, `/readyz` via endpoint.
8. Event with a reminder to remove the node from the LB pool.

No `kubeadm-config` apiEndpoints cleanup is needed in 1.25: ClusterStatus is gone and endpoints come from pod annotations.

Dead node: drain and reset are skipped, Node deleted. For a master:

- Unreachable for Salt but its etcd member is healthy → refuse: it is a Salt connectivity issue, and removing it would leave an isolated etcd running.
- Removing a dead master requires `node_dead: true`, and the runner verifies the member is really unhealthy.

After removal the event reminds to add the node to `decommissioned` or drop it from the nodegroup; otherwise the next join re-adds it. Replacing a master = remove-node, then join-controlplane.

### 6.5 reconfigure-kubelet and reconfigure-controlplane

Both run `kubeadm.reconfigure` node by node and accept a `targets` param, so one node, a group or all nodes can be processed.

reconfigure-kubelet (ConfigMap first, because `upgrade node phase kubelet-config` reads it):

1. `diff` of `kubeadm:kubelet` vs `kubelet-config`, sent as an event.
2. `upload_config kubelet` from a master.
3. Masters one by one, workers in batches; `drain: false` by default (fields that need drain are denylisted), `drain: true` available.
4. On failure the node rolls back itself and the scenario stops; the event says the ConfigMap is already new: fix pillar and re-run, or revert the value.

reconfigure-controlplane (ConfigMap last):

1. `diff` of `kubeadm:cluster` vs `kubeadm-config`.
2. Masters one by one; orchestrate passes ClusterConfiguration from pillar into the step pillar, so nodes do not read the ConfigMap; node overlay applied on top.
3. Only if all masters succeed: `upload_config kubeadm`. A failure on master 2 leaves the old ConfigMap, matching the majority.
4. certSANs change: per master delete `apiserver.crt`/`.key`, then `kubeadm init phase certs apiserver --config node.yaml`, then restart apiserver.

### 6.6 certs

What expires in 1.25: CAs after 10 years (monitored only); component certificates (apiserver, apiserver-kubelet-client, apiserver-etcd-client, front-proxy-client, etcd server/peer/healthcheck-client) and kubeconfigs (`admin.conf`, `controller-manager.conf`, `scheduler.conf`) after 1 year. `kubelet.conf` points to the self-rotated `kubelet-client-current.pem`.

Single implementation: the renew script, used by the systemd timer and by `orch.kubeadm.certs` alike.

1. `kubeadm certs check-expiration --config <local config>`: local config so the check works even when the apiserver is down because of an expired certificate.
2. Renew only if days left < `threshold_days` (default 30) or `--force`.
3. Safety gate: all etcd members healthy and `/readyz` OK, otherwise exit non-zero without changes. This replaces orchestration for the timer: a broken master 1 blocks master 2.
4. Backup `/etc/kubernetes/pki` and `*.conf`.
5. `kubeadm certs renew all --config …` (SANs are kept from existing certs).
6. Restart static pods one by one by moving the manifest out and back, waiting for the pod to disappear: etcd → healthy → apiserver → `/readyz` → controller-manager, scheduler.
7. Verify new expiry and health.
8. On failure restore from backup and restart components (works only while the old certs are still valid, hence the 30-day margin).

Kubelet is not restarted. Orchestrate `certs` collects expiry from all nodes, then runs the script on masters one by one with an etcd quorum check before each.

Kubelet certificates:

- Client cert rotation is on by default (`rotateCertificates: true`); the formula checks expiry. An expired client cert (node offline for long) means remove-node + join.
- Serving cert with `serverTLSBootstrap: true` creates CSRs that kube-controller-manager does not approve; the formula only reports pending CSRs and never approves them blindly. An approver belongs to GitOps.

`admin.conf` is renewed by `renew all`; `pick_source` skips masters with an expired `admin.conf`.

### 6.7 upgrade

Rules checked at render: minor versions one at a time (patch releases freely), kubelet never newer than apiserver, target kubeadm on the first master before `upgrade apply`.

Prechecks (read-only): skew rules, all nodes Ready, etcd healthy, certs not near expiry, disk space for kubeadm backups, per-node versions. A partly upgraded cluster is resumed: done nodes are skipped. Deprecated API usage is checked outside the formula (kubent or pluto), listed in the README checklist.

Order:

0. **etcd snapshot** on the source master to `/var/lib/etcd-backup/` with date and version: the only way back for the whole cluster (no minor downgrade in kubeadm).
1. **First master**: switch `kubeadm` symlink → `kubeadm upgrade plan` (event) → `kubeadm upgrade apply vX.Y.Z -y --patches …` (upgrades control plane, etcd, CoreDNS, kube-proxy, ConfigMaps, renews certs; kubeadm backs up manifests and etcd into `/etc/kubernetes/tmp/` and rolls back this node's control plane itself) → re-run `kubeadm.reconfigure` if the node has an overlay → run `kubeadm.addons` to restore formula-owned addon settings → drain → switch `kubelet`/`kubectl` symlinks → restart kubelet → checks → uncordon.
2. **Other masters**, one by one with a quorum check: same, with `kubeadm upgrade node --patches …`.
3. **Workers** in batches: switch kubeadm → `kubeadm upgrade node --patches …` → drain → switch kubelet → restart → checks → uncordon.

Rollback:

- Kubelet fails to start: switch the kubelet symlink back and restart (old kubelet with new control plane is within skew), stop the scenario.
- Control plane on a master: rolled back by kubeadm from its backup; our state checks health and stops.
- Whole cluster: manual restore from the etcd snapshot; never automated.

Cross-formula items:

- pause image: kubeadm expects a specific pause version per release; sandbox image is set by the containerd formula. Use a shared version table in pillar, or have the containerd formula read it from `kubeadm` pillar.
- With a custom Corefile add `--ignore-preflight-errors=CoreDNSUnsupportedPlugins,CoreDNSMigration` to `upgrade apply`.
- Addons with `managed_by: external` are not touched; the precheck verifies kubeadm will not reinstall them.

### 6.8 addons

Runs `kubeadm.addons` on one healthy master.

kube-proxy (`managed_by: kubeadm`):

1. Render KubeProxyConfiguration, diff vs ConfigMap `kube-proxy`.
2. On change: `kubeadm init phase addon kube-proxy --config …` (keeps the kubeadm format; kubeadm reads this ConfigMap on upgrade, so changes survive).
3. `kubectl rollout restart ds/kube-proxy`, `rollout status` with timeout.
4. DaemonSet patch via server-side apply, `--field-manager=salt-kubeadm`.
5. Switching `mode` iptables ↔ ipvs leaves old rules on nodes and needs ipvs kernel modules (containerd formula): warning emitted.

CoreDNS (`managed_by: kubeadm`):

1. Corefile from pillar (raw text) applied to ConfigMap `coredns`; no restart, the `reload` plugin picks it up.
2. Deployment patch via server-side apply, then `rollout status`.
3. Checks: pods Ready; optional DNS query for `kubernetes.default.svc.cluster.local` against the DNS service ClusterIP from a master.

Rollback: ConfigMaps, DaemonSet and Deployment saved to `/etc/kubernetes/salt-backup/addons/<timestamp>/` on the master; on failure restore and `rollout undo`.

`managed_by: external` (e.g. Cilium kube-proxy replacement, CoreDNS Helm chart via Flux): `--skip-phases` at init, skipped by addons and upgrade. Switching kubeadm → external is allowed (the formula stops managing it); external → kubeadm is not done by the formula (the Helm release must be removed on the GitOps side first).

## 7. Cross-cutting concerns

Four rules apply to every scenario: `test=True` never changes anything, progress is visible in real time, every check is bounded in time, and every change has a backup and a rollback path.

### test=True

Dangers to avoid:

- Jinja calls to `saltutil.runner` at render time always execute, regardless of `test`.
- `salt.runner` steps in orchestrate likely execute for real in test mode (verify on the target Salt version; design assumes the worst).
- Nested orchestrates are started through `salt.runner state.orchestrate`, so they may run for real.
- `unless` / `onlyif` run in test mode.

Rules:

1. Read-only runner functions may be called at render; mutating ones never (section 4). Mutating functions check `__opts__['test']` and return `result: None` with a description.
2. Every nested orchestrate step passes `test: {{ opts['test'] }}` explicitly; CI verifies a test run changes nothing.
3. All kubeadm operations go through `_states/kubeadm.py` with real diffs.
4. `unless` / `onlyif` contain only read-only commands.
5. No separate `dry_run` flag: `test=True` is the dry run.

What a test run shows:

- Pillar errors: denylist and validation fail at render.
- Per node: binaries to download, diffs of unit, flags and patches, resulting kubelet `config.yaml` diff, control-plane manifest diffs.
- Per cluster: diff of pillar vs `kubeadm-config`, `kubelet-config` and addon ConfigMaps; nodes selected for join/remove; chosen source; etcd quorum precheck result; `upgrade plan`.

Limit: steps that depend on earlier results (labels after join, postchecks) can only report "would".

### Progress events

- Orchestrate emits `salt.runner event.send` before and after each node and each major step; nodes never send events (salt-ssh has no event bus).
- Tag scheme: `kubeadm/<scenario>/<step>/<node>`, e.g. `kubeadm/bootstrap/start/master-1`, `kubeadm/join-controlplane/etcd-healthy/master-2`, `kubeadm/bootstrap/done/master-1`.
- Watch live with `salt-run state.event pretty=True`, or run orchestrate asynchronously and watch events only.
- To work node by node manually, every scenario accepts `targets` with one node.

### Health checks

Short checks with Salt `retry` (defaults: 30 attempts, 10 s interval, from `kubeadm:orchestrate:health`), never one long wait. A failed check fails the step; the scenario stops on that node.

| Check | Used by |
| --- | --- |
| Mirror pod annotation hash equals the new manifest | reconfigure, certs, upgrade |
| Local apiserver `/readyz` | all control-plane steps |
| etcd `/health` on metrics port 2381 (localhost) | all control-plane steps |
| etcdctl `member list` + `endpoint health` on all members | join, remove, certs, upgrade |
| Node Ready (not required before CNI) | join with `wait_ready`, reconfigure, upgrade |
| kubelet active | all node steps |
| `/readyz` via controlPlaneEndpoint | bootstrap guard, remove-node postcheck |

### Backups and rollback

| Scenario | Backup | Automatic rollback |
| --- | --- | --- |
| reconfigure | kubelet `config.yaml`, manifests → `/etc/kubernetes/salt-backup/<ts>/` | restore + kubelet restart |
| join-controlplane | — | remove unstarted etcd member + `kubeadm reset` |
| join-worker | — | `kubeadm reset` + delete Node (unless `keep_failed`) |
| certs | `/etc/kubernetes/pki`, `*.conf` | restore + restart static pods |
| upgrade | etcd snapshot, kubeadm's own backup in `/etc/kubernetes/tmp/` | kubelet symlink back; control plane by kubeadm; cluster restore manual |
| addons | ConfigMaps, DaemonSet, Deployment → `salt-backup/addons/<ts>/` | restore + `rollout undo` |

### Secrets handling

- `admin.conf` only on masters; cluster operations run on a master with `--kubeconfig /etc/kubernetes/admin.conf`.
- Workers use `kubelet.conf`; kubeadm RBAC lets nodes read `kubeadm-config` and `kubelet-config`.
- Tokens: TTL 15 min, passed through step pillar only, rendered to a 0600 file without diff output, deleted after use; never on the command line.
- Secret `kubeadm-certs` deleted after the last control-plane join.
- GitOps bootstrap reactor runs its command on a master, not on the Salt master.

## 8. Iterative rollout plan

The formula ships in ten phases; each phase ends with a usable, tested increment and only depends on earlier phases, so it can be released and used before the next one starts.

Every phase is done when its acceptance items pass on a test cluster over BOTH transports (minion and salt-ssh) and a `test=True` run of everything delivered so far changes nothing.

### Phase 0 — Foundations

Deliverables:

- [ ] Repository from template-formula; `defaults.yaml`, `map.jinja`, `macros.jinja`
- [ ] `_modules/kubeadm_util.py`: `merge_layers`, `to_k8s`, `extra_args`, `check_denylist`
- [ ] Denylists and named-list key set in `defaults.yaml`
- [ ] Pillar examples (cluster, roles, node, orchestrate)
- [ ] CI: lint, pytest for `kubeadm_util`, render of all SLS with example pillar
- [ ] Test environment: VMs for 3 masters + 2 workers, external LB, both transports configured

Acceptance:

- [ ] Merge results match expected YAML for role and node overrides, including named lists
- [ ] A denylisted key in node pillar fails rendering with a clear message

### Phase 1 — Node install

Deliverables:

- [ ] `kubeadm.binaries` (versioned dirs, sha256, symlinks only on new nodes, keep 2 versions)
- [ ] `kubeadm.kubelet` (unit, drop-in, `/etc/default/kubelet`)
- [ ] `kubeadm.config` (config documents, patches dir with `clean: true`)

Acceptance:

- [ ] Highstate on a fresh VM is idempotent (second run: no changes)
- [ ] Changing `kubeadm:version` only stages new binaries; active symlinks stay
- [ ] Removing a patch from pillar removes its file

### Phase 2 — Bootstrap

Deliverables:

- [ ] `_states/kubeadm.py`: `initialized`, `healthy`
- [ ] `_runners/kubeadm.py`: transport layer (LocalClient / SSHClient), `cluster_exists`, `pick_init_node`, `pick_source`
- [ ] `orch.kubeadm.bootstrap` with events, endpoint precheck, `managed_by` skip-phases
- [ ] `kubeadm.reset` with `confirm_reset`
- [ ] README: LB requirements

Acceptance:

- [ ] Single-master cluster created over minion and over salt-ssh
- [ ] Second bootstrap run is refused; refused also with the LB down
- [ ] `test=True` bootstrap creates nothing; events visible in `state.event`

### Phase 3 — Workers

Deliverables:

- [ ] Runner `join_node` (mutating, test-aware), `nodes`, `pending_nodes`, `node_meta`
- [ ] `_states/kubeadm.py`: `joined`
- [ ] `orch.kubeadm.join-worker` + `join-worker-batch` with `batch_size`, `fail_fast`, `max_fail`, `keep_failed`, `wait_ready`, `replace`
- [ ] Labels and taints with managed-labels annotation; `decommissioned` list

Acceptance:

- [ ] 2 workers joined in batches; tokens never appear in state output or `ps`
- [ ] Induced failure: failed worker reset and removed, next batch not started
- [ ] Label removed from pillar is removed from the Node; foreign labels untouched

### Phase 4 — HA control plane

Deliverables:

- [ ] Runners `cp_members`, `etcd_precheck`, `etcd_postcheck`, `join_rollback`
- [ ] `orch.kubeadm.join-controlplane` + `join-controlplane-node` with fresh certificate key per master
- [ ] `kubeadm-certs` Secret cleanup; even-count warning

Acceptance:

- [ ] 1 → 3 masters, one at a time, etcd healthy after each
- [ ] Join refused while one etcd member is unhealthy
- [ ] Induced failure after member add: rollback leaves `member list` as before
- [ ] Join works with one master stopped (source auto-selection)

### Phase 5 — Node removal

Deliverables:

- [ ] Runners `etcd_member_remove`, `move_leader`
- [ ] `orch.kubeadm.remove-node` for worker and master, `node_dead` logic, drain options, `purge`

Acceptance:

- [ ] Worker removed with PDB respected; blocked drain reports the pods
- [ ] Master replaced (remove + join) keeping quorum; leader moved first when needed
- [ ] Unreachable-for-Salt master with healthy etcd member is NOT removed
- [ ] Removing the last master is refused

### Phase 6 — Reconfigure

Deliverables:

- [ ] `kubeadm.reconfigure` with backup and automatic rollback
- [ ] `_states/kubeadm.py`: `kubelet_config_applied`, `control_plane_applied` with real diffs
- [ ] Runners `diff`, `upload_config`
- [ ] `orch.kubeadm.reconfigure-kubelet`, `reconfigure-controlplane`, certSANs re-issue

Acceptance:

- [ ] `systemReserved` changed on one worker only via `state.apply kubeadm.reconfigure`
- [ ] apiserver resources changed on one master only; survives a later `kubeadm upgrade node` (patches)
- [ ] Broken apiserver flag rolls back automatically and stops the scenario
- [ ] `test=True` shows the diff of the resulting kubelet `config.yaml` and manifests

### Phase 7 — Certificates

Deliverables:

- [ ] Renew script with `--check`, `--force`, threshold, safety gate, backup, ordered restarts, rollback
- [ ] `kubeadm.certs` (script + staggered systemd timer, `auto_renew` flag), `kubeadm.certs.check`
- [ ] `orch.kubeadm.certs`, runner `cert_expiry`; `_states/kubeadm.py`: `certs_renewed`

Acceptance:

- [ ] Forced renewal on 3 masters sequentially; cluster healthy throughout
- [ ] Timer run skips renewal when etcd is unhealthy
- [ ] Expiry event emitted by the scheduled check

### Phase 8 — Addons

Deliverables:

- [ ] `kubeadm.addons` for kube-proxy (ConfigMap via kubeadm phase, DaemonSet SSA, rollout) and CoreDNS (Corefile, Deployment SSA)
- [ ] `orch.kubeadm.addons`; `managed_by: kubeadm | external`
- [ ] `_states/kubeadm.py`: `addon_applied` with `kubectl diff`

Acceptance:

- [ ] Corefile change applied without restart; replicas and resources changed
- [ ] Failed rollout restored automatically
- [ ] `external` addon is never touched

### Phase 9 — Upgrade

Deliverables:

- [ ] Runners `versions`, `etcd_snapshot`; skew checks at render
- [ ] `orch.kubeadm.upgrade` + `upgrade-node`; symlink switching; overlay re-apply; addons re-apply
- [ ] `_states/kubeadm.py`: `upgraded` with `upgrade plan` / `--dry-run`
- [ ] pause image version table shared with the containerd formula
- [ ] README checklist: deprecated APIs (kubent / pluto), snapshot location

Acceptance:

- [ ] Patch upgrade within 1.25 and minor upgrade 1.25 → 1.26 on the test cluster
- [ ] Interrupted upgrade resumes and skips upgraded nodes
- [ ] Skipping a minor version is refused at render
- [ ] Kubelet failure on a worker reverts its symlink and stops the scenario

## 9. Open questions and items to verify

These items were left open in design or depend on behaviour that must be confirmed on a test cluster before the related phase is closed.

| # | Item | Phase | Default until resolved |
| --- | --- | --- | --- |
| 1 | conntrack and socat: who installs them (kubeadm preflight requires them) | 1 | open, to be clarified |
| 2 | `N@` nodegroup matcher in pillar top and orchestrate targets under salt-ssh on the used Salt version | 0 | explicit `L@` lists |
| 3 | `salt.runner` steps and nested `state.orchestrate` behaviour under `test=True` | 2 | assume they execute; explicit `test` passing + test-aware runners |
| 4 | `kubeletconfiguration` patch target via `upgrade node phase kubelet-config --patches` on 1.25 | 6 | patches; verify on the lowest version |
| 5 | etcd in reconfigure: `init phase etcd local` on an already-joined member | 6 | `etcd` target excluded from reconfigure in v1 |
| 6 | How to render control-plane manifests into a temp dir for `test=True` diffs on 1.25 (dry-run of the phase or another way) | 6 | report "would run" if not possible |
| 7 | kubeadm 1.25 upgrade behaviour when kube-proxy / CoreDNS were skipped at init | 8, 9 | precheck blocks upgrade if a reinstall is detected |
| 8 | Where to copy the pre-upgrade etcd snapshot off the master | 9 | stays in `/var/lib/etcd-backup/` on the source master |
| 9 | pause image version table: owned by this formula or the containerd formula | 9 | shared pillar key, read by both |
| 10 | Kubelet drop-in dir (`--config-dir`) switch when the minimum version reaches 1.30 | later | patches |
| 11 | Supported Kubernetes versions beyond 1.25 and the upgrade target policy | later | out of scope |

From 1.30 the kubelet drop-in mechanism becomes available and `map.jinja` selects it by version without changing the pillar format; from 1.31 (`v1beta4`) `extraArgs` render as a list, handled by `kubeadm_util.extra_args`.
