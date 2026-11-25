# Phase 9 — Upgrade

## Цель

Upgrade Kubernetes версии: patch и minor. etcd snapshot, symlink switching, ordered upgrade masters → workers, overlay re-apply, addons re-apply.

## Зависимости

- Phase 1: binaries (versioned dirs, symlinks)
- Phase 4: etcd checks, quorum
- Phase 6: reconfigure (overlay re-apply)
- Phase 7: certs (expiry check)
- Phase 8: addons (re-apply after upgrade)

## Что реализуем

### 1. `_runners/kubeadm.py` — дополнения

#### `versions(nodegroup, transport)` — read-only
- kubelet version per Node: `kubectl get nodes -o json` → `status.nodeInfo.kubeletVersion`
- Control-plane image versions: `kubectl get pods -n kube-system -o json` → container image tags
- kubeadm version per node: `kubeadm version -o short`
- Возвращает: `{nodes: {name: {kubelet, kubeadm, images: {component: version}}}`

#### `etcd_snapshot(source, transport)` — MUTATING
- **test mode**: описание "would take etcd snapshot on {source}"
- `etcdctl snapshot save /var/lib/etcd-backup/snapshot-{date}-{from_version}.db` через exec в etcd pod
- Verify snapshot: `etcdctl snapshot status`
- Возвращает: path, size, status

### 2. `_states/kubeadm.py` — дополнение

#### `kubeadm.upgraded`
- **Apply first master**: `kubeadm upgrade apply {version} -y --patches {patches_dir}`
  - С `managed_by: external`: `--ignore-preflight-errors=CoreDNSUnsupportedPlugins,CoreDNSMigration` (если custom Corefile)
- **Apply other masters/workers**: `kubeadm upgrade node --patches {patches_dir}`
- **test=True**:
  - First master: `kubeadm upgrade plan` → показывает план
  - Others: `kubeadm upgrade node --dry-run` (если поддерживается)
  - `result: None` с upgrade plan output

### 3. Skew и prechecks (render time)

В `map.jinja` или в runner:

```python
def _upgrade_prechecks(source, target_version, nodegroup, transport):
    """Проверки перед upgrade."""
    current = versions(nodegroup, transport)

    # 1. Minor version: only one at a time
    for node, info in current["nodes"].items():
        current_minor = parse_minor(info["kubelet"])
        target_minor = parse_minor(target_version)
        if target_minor - current_minor > 1:
            raise ValueError(
                f"Cannot skip minor versions: {node} is on {info['kubelet']}, "
                f"target is {target_version}. Upgrade to {current_minor+1} first."
            )

    # 2. kubelet never newer than apiserver
    # (checked during rolling upgrade — workers upgraded after masters)

    # 3. All nodes Ready
    for node, info in current["nodes"].items():
        if info["status"] != "Ready":
            raise ValueError(f"Node {node} is not Ready")

    # 4. etcd healthy
    # 5. Certs not near expiry
    # 6. Disk space for kubeadm backups

    return {"ok": True, "plan": plan_output}
```

### 4. `orch/kubeadm/upgrade.sls`

Outer orchestrate:

```
Prechecks (read-only):
  - Skew rules
  - All nodes Ready
  - etcd healthy
  - Certs not near expiry (cert_expiry, warn if < 30 days)
  - Disk space
  - Per-node versions
  - Partly upgraded → resume (skip done nodes)

0. etcd_snapshot на source master
   → /var/lib/etcd-backup/snapshot-{date}-{from_version}.db
   Event: kubeadm/upgrade/etcd-snapshot/<source>

1. First master:
   a. Switch kubeadm symlink → target version
   b. kubeadm upgrade plan → event
   c. kubeadm upgrade apply {version} -y --patches
      (upgrades CP, etcd, CoreDNS, kube-proxy, ConfigMaps, renews certs)
   d. Re-run kubeadm.reconfigure if node has overlay
   e. Run kubeadm.addons to restore formula-owned addon settings
   f. Drain (cordon + kubectl drain)
   g. Switch kubelet, kubectl symlinks → target version
   h. Restart kubelet
   i. Health checks: kubelet, /readyz, etcd, node Ready
   j. Uncordon
   k. Event

2. Other masters (one by one, quorum check before each):
   a. Switch kubeadm symlink
   b. kubeadm upgrade node --patches
   c. Re-run reconfigure if overlay
   d. Drain
   e. Switch kubelet, kubectl symlinks
   f. Restart kubelet
   g. Health checks
   h. Uncordon
   i. Event

3. Workers (in batches):
   a. Switch kubeadm symlink
   b. kubeadm upgrade node --patches
   c. Drain
   d. Switch kubelet symlink
   e. Restart kubelet
   f. Health checks
   g. Uncordon
   h. Event per batch
```

### 5. `orch/kubeadm/upgrade-node.sls`

Nested per-node orchestrate:

```yaml
# Symlink switch for kubeadm
switch_kubeadm:
  file.symlink:
    - name: {{ bin_dir }}/kubeadm
    - target: {{ install_dir }}/{{ target_version }}/kubeadm
    - force: True

# Upgrade
upgrade_node:
  kubeadm.upgraded:
    - version: {{ target_version }}
    - patches: {{ patches_dir }}
    - first_master: {{ is_first_master }}
    - require:
      - file: switch_kubeadm

# Reconfigure overlay (if applicable)
{% if has_overlay %}
reconfigure_overlay:
  salt.state:
    - sls: kubeadm.reconfigure
    - require:
      - kubeadm: upgrade_node
{% endif %}

# Addons re-apply (first master only)
{% if is_first_master %}
addons_reapply:
  salt.state:
    - sls: kubeadm.addons
    - require:
      - kubeadm: upgrade_node
{% endif %}

# Drain
drain_node:
  module.run:
    - name: kubeadm_util.drain
    - node: {{ target }}
    - timeout: {{ drain_timeout }}
    - require:
      - kubeadm: upgrade_node

# Switch kubelet + kubectl symlinks
switch_kubelet:
  file.symlink:
    - name: {{ bin_dir }}/kubelet
    - target: {{ install_dir }}/{{ target_version }}/kubelet
    - force: True
    - require:
      - module: drain_node

switch_kubectl:
  file.symlink:
    - name: {{ bin_dir }}/kubectl
    - target: {{ install_dir }}/{{ target_version }}/kubectl
    - force: True
    - require:
      - module: drain_node

# Restart kubelet
restart_kubelet:
  service.running:
    - name: kubelet
    - watch:
      - file: switch_kubelet

# Health checks
upgrade_health:
  kubeadm.healthy:
    - checks: [kubelet, readyz, etcd, node_ready]
    - retries: {{ health.retries }}
    - interval: {{ health.interval }}
    - require:
      - service: restart_kubelet

# Uncordon
uncordon_node:
  module.run:
    - name: kubeadm_util.uncordon
    - node: {{ target }}
    - require:
      - kubeadm: upgrade_health
```

### 6. Rollback logic

```
Kubelet fails to start:
  → Switch kubelet symlink BACK to old version
  → Restart kubelet
  → Old kubelet + new CP = within skew
  → Scenario STOPS

Control plane fails on a master:
  → kubeadm rolls back from its own backup (/etc/kubernetes/tmp/)
  → Health check catches failure
  → Scenario STOPS

Whole cluster:
  → Manual restore from etcd snapshot
  → NEVER automated (too risky)
```

### 7. Cross-formula: pause image version table

```yaml
# Shared pillar key, read by both formulas
kubernetes:
  version_table:
    v1.25.16:
      pause_image: registry.k8s.io/pause:3.8
    v1.26.15:
      pause_image: registry.k8s.io/pause:3.9
    v1.27.16:
      pause_image: registry.k8s.io/pause:3.9
```

---

## TDD: тесты

### Файл: `test/unit/test_runner_upgrade.py`

```python
"""
Тесты для runner функций upgrade.
"""
import pytest
from unittest.mock import MagicMock


class TestVersions:
    """Runner kubeadm.versions."""

    def test_returns_per_node_versions(self, runner_mod):
        """Возвращает версии per node."""
        runner_mod._cmd = MagicMock(return_value={
            "master-1": '{"items": [{"metadata": {"name": "master-1"}, '
                        '"status": {"nodeInfo": {"kubeletVersion": "v1.25.16"}}}]}'
        })
        result = runner_mod.versions(nodegroup="k8s-masters", transport="minion")
        assert "master-1" in result["nodes"]
        assert result["nodes"]["master-1"]["kubelet"] == "v1.25.16"


class TestEtcdSnapshot:
    """Runner kubeadm.etcd_snapshot."""

    def test_test_mode(self, runner_mod):
        runner_mod.__opts__["test"] = True
        result = runner_mod.etcd_snapshot(source="master-1", transport="minion")
        assert result["result"] is None

    def test_takes_snapshot(self, runner_mod):
        runner_mod._cmd = MagicMock(return_value={"master-1": "Snapshot saved"})
        result = runner_mod.etcd_snapshot(source="master-1", transport="minion")
        assert result["result"] is True
        cmd_str = str(runner_mod._cmd.call_args_list)
        assert "snapshot save" in cmd_str

    def test_snapshot_path_includes_version(self, runner_mod):
        """Snapshot filename включает текущую версию."""
        runner_mod._cmd = MagicMock(return_value={"master-1": "Snapshot saved"})
        runner_mod._current_version = MagicMock(return_value="v1.25.16")
        result = runner_mod.etcd_snapshot(source="master-1", transport="minion")
        assert "v1.25.16" in result.get("path", "")


class TestUpgradePrechecks:
    """Upgrade prechecks."""

    def test_skip_minor_refused(self, runner_mod):
        """Пропуск minor версии → refuse."""
        runner_mod.versions = MagicMock(return_value={
            "nodes": {
                "master-1": {"kubelet": "v1.25.16", "kubeadm": "v1.25.16"},
            }
        })
        with pytest.raises(ValueError, match="[Ss]kip"):
            runner_mod._upgrade_prechecks(
                source="master-1",
                target_version="v1.27.0",
                nodegroup="k8s-masters",
                transport="minion",
            )

    def test_patch_upgrade_allowed(self, runner_mod):
        """Patch upgrade OK."""
        runner_mod.versions = MagicMock(return_value={
            "nodes": {
                "master-1": {"kubelet": "v1.25.14", "kubeadm": "v1.25.14"},
            }
        })
        runner_mod._etcd_healthy = MagicMock(return_value=True)
        runner_mod._all_nodes_ready = MagicMock(return_value=True)
        runner_mod._certs_ok = MagicMock(return_value=True)
        result = runner_mod._upgrade_prechecks(
            source="master-1",
            target_version="v1.25.16",
            nodegroup="k8s-masters",
            transport="minion",
        )
        assert result["ok"] is True

    def test_minor_upgrade_one_step(self, runner_mod):
        """Minor upgrade на 1 шаг OK."""
        runner_mod.versions = MagicMock(return_value={
            "nodes": {
                "master-1": {"kubelet": "v1.25.16", "kubeadm": "v1.25.16"},
            }
        })
        runner_mod._etcd_healthy = MagicMock(return_value=True)
        runner_mod._all_nodes_ready = MagicMock(return_value=True)
        runner_mod._certs_ok = MagicMock(return_value=True)
        result = runner_mod._upgrade_prechecks(
            source="master-1",
            target_version="v1.26.0",
            nodegroup="k8s-masters",
            transport="minion",
        )
        assert result["ok"] is True

    def test_not_ready_node_refused(self, runner_mod):
        """NotReady node → refuse."""
        runner_mod.versions = MagicMock(return_value={
            "nodes": {
                "master-1": {"kubelet": "v1.25.16", "status": "Ready"},
                "worker-1": {"kubelet": "v1.25.16", "status": "NotReady"},
            }
        })
        with pytest.raises(ValueError, match="[Nn]ot Ready"):
            runner_mod._upgrade_prechecks(
                source="master-1",
                target_version="v1.25.17",
                nodegroup="all",
                transport="minion",
            )

    def test_resume_partly_upgraded(self, runner_mod):
        """Partly upgraded cluster → resume, skip done nodes."""
        runner_mod.versions = MagicMock(return_value={
            "nodes": {
                "master-1": {"kubelet": "v1.26.0"},  # already done
                "master-2": {"kubelet": "v1.25.16"},  # needs upgrade
                "worker-1": {"kubelet": "v1.25.16"},  # needs upgrade
            }
        })
        runner_mod._etcd_healthy = MagicMock(return_value=True)
        runner_mod._all_nodes_ready = MagicMock(return_value=True)
        runner_mod._certs_ok = MagicMock(return_value=True)
        result = runner_mod._upgrade_prechecks(
            source="master-1",
            target_version="v1.26.0",
            nodegroup="all",
            transport="minion",
        )
        assert result["ok"] is True
        assert "master-1" not in result.get("pending_nodes", [])
        assert "master-2" in result.get("pending_nodes", [])
```

### Файл: `test/unit/test_state_upgraded.py`

```python
"""
Тесты для state kubeadm.upgraded.
"""
import pytest
from unittest.mock import MagicMock


class TestKubeadmUpgraded:
    """State kubeadm.upgraded."""

    def test_first_master_upgrade_apply(self, state_mod):
        """First master → kubeadm upgrade apply."""
        state_mod.__salt__["cmd.run_all"] = MagicMock(return_value={
            "retcode": 0, "stdout": "upgraded", "stderr": ""
        })
        result = state_mod.upgraded(
            name="upgrade", version="v1.26.0",
            patches="/etc/kubernetes/patches",
            first_master=True,
        )
        cmd_str = state_mod.__salt__["cmd.run_all"].call_args[0][0]
        assert "upgrade apply" in cmd_str
        assert "v1.26.0" in cmd_str
        assert "-y" in cmd_str

    def test_other_nodes_upgrade_node(self, state_mod):
        """Non-first nodes → kubeadm upgrade node."""
        state_mod.__salt__["cmd.run_all"] = MagicMock(return_value={
            "retcode": 0, "stdout": "upgraded", "stderr": ""
        })
        result = state_mod.upgraded(
            name="upgrade", version="v1.26.0",
            patches="/etc/kubernetes/patches",
            first_master=False,
        )
        cmd_str = state_mod.__salt__["cmd.run_all"].call_args[0][0]
        assert "upgrade node" in cmd_str
        assert "--patches" in cmd_str

    def test_test_mode_shows_plan(self, state_mod):
        """test=True first master → kubeadm upgrade plan."""
        state_mod.__opts__["test"] = True
        state_mod.__salt__["cmd.run_all"] = MagicMock(return_value={
            "retcode": 0,
            "stdout": "Components that must be upgraded:\n...",
            "stderr": ""
        })
        result = state_mod.upgraded(
            name="upgrade", version="v1.26.0",
            first_master=True,
        )
        assert result["result"] is None
        cmd_str = state_mod.__salt__["cmd.run_all"].call_args[0][0]
        assert "upgrade plan" in cmd_str
```

### Файл: `test/unit/test_orch_upgrade.py`

```python
"""
Тесты для orch/kubeadm/upgrade.sls.
"""
import pytest


class TestOrchUpgrade:
    """Orchestrate upgrade."""

    def test_etcd_snapshot_first(self, render_orch):
        """etcd snapshot — первый mutating шаг."""
        states = render_orch("orch/kubeadm/upgrade.sls", orch_pillar())
        snapshot = next(i for i, s in enumerate(states) if "etcd_snapshot" in str(s))
        first_upgrade = next(i for i, s in enumerate(states) if "upgrade" in str(s.get("sls", "")))
        assert snapshot < first_upgrade

    def test_first_master_upgrade_apply(self, render_orch):
        """First master uses upgrade apply."""
        states = render_orch("orch/kubeadm/upgrade.sls", orch_pillar())
        first = next(s for s in states if "upgrade" in str(s.get("sls", ""))
                     and s.get("pillar", {}).get("kubeadm", {}).get("_first_master"))
        assert first is not None

    def test_masters_before_workers(self, render_orch):
        """Masters upgraded before workers."""
        states = render_orch("orch/kubeadm/upgrade.sls", orch_pillar())
        # All master upgrade steps before any worker upgrade step
        pass  # depends on rendering

    def test_drain_and_uncordon(self, render_orch):
        """Each node: drain before symlink switch, uncordon after health."""
        states = render_orch("orch/kubeadm/upgrade-node.sls",
                            orch_pillar(target="master-1"))
        drain_idx = next(i for i, s in enumerate(states) if "drain" in str(s).lower())
        symlink_idx = next(i for i, s in enumerate(states)
                          if "symlink" in str(s).lower() and "kubelet" in str(s).lower())
        uncordon_idx = next(i for i, s in enumerate(states) if "uncordon" in str(s).lower())
        health_idx = next(i for i, s in enumerate(states) if "healthy" in str(s).lower())
        assert drain_idx < symlink_idx
        assert health_idx < uncordon_idx

    def test_symlink_switch_uses_file_symlink(self, render_orch):
        """Symlink switch через file.symlink, не cmd.run ln."""
        states = render_orch("orch/kubeadm/upgrade-node.sls",
                            orch_pillar(target="master-1"))
        symlinks = [s for s in states if s.get("fun") == "file.symlink"]
        assert len(symlinks) >= 2  # kubeadm + kubelet (+ kubectl)

    def test_kubelet_rollback_on_failure(self, render_orch):
        """Kubelet failure → symlink back + restart."""
        states = render_orch("orch/kubeadm/upgrade-node.sls",
                            orch_pillar(target="worker-1"))
        rollback = [s for s in states if "rollback" in str(s).lower() and "kubelet" in str(s).lower()]
        assert len(rollback) >= 1

    def test_overlay_reapply_for_masters(self, render_orch):
        """Master with overlay → reconfigure after upgrade."""
        pillar = orch_pillar(target="master-1")
        pillar["kubeadm"]["orchestrate"]["_has_overlay"] = True
        states = render_orch("orch/kubeadm/upgrade-node.sls", pillar)
        reconfigure = [s for s in states if "reconfigure" in str(s.get("sls", ""))]
        assert len(reconfigure) >= 1

    def test_addons_reapply_first_master(self, render_orch):
        """Addons re-apply on first master."""
        pillar = orch_pillar(target="master-1")
        pillar["kubeadm"]["orchestrate"]["_first_master"] = True
        states = render_orch("orch/kubeadm/upgrade-node.sls", pillar)
        addons = [s for s in states if "addons" in str(s.get("sls", ""))]
        assert len(addons) >= 1
```

---

## TDD: порядок работы

### Шаг 1 — Тесты
1. `test/unit/test_runner_upgrade.py`
2. `test/unit/test_state_upgraded.py`
3. `test/unit/test_orch_upgrade.py`

### Шаг 2 — Валидация
- Minor skip refused
- Patch freely
- Resume partly upgraded
- etcd snapshot first
- Symlink via file.symlink, не cmd.run
- Drain before switch, uncordon after health
- Kubelet rollback on failure

### Шаг 3 — Реализация
1. `_runners/kubeadm.py`: `versions`, `etcd_snapshot`, `_upgrade_prechecks`
2. `_states/kubeadm.py`: `upgraded`
3. `orch/kubeadm/upgrade.sls`
4. `orch/kubeadm/upgrade-node.sls`
5. Pause image version table в pillar

### Шаг 4 — Тестирование
1. Unit: `pytest test/unit/ -v`
2. Patch upgrade 1.25.14 → 1.25.16
3. Minor upgrade 1.25 → 1.26
4. Interrupted upgrade resumes, skips done nodes
5. Minor skip refused at render
6. Kubelet failure on worker → symlink reverted, scenario stops

---

## Acceptance criteria

- [ ] Patch upgrade в рамках 1.25 успешен
- [ ] Minor upgrade 1.25 → 1.26 успешен
- [ ] Прерванный upgrade возобновляется, пропускает upgraded nodes
- [ ] Пропуск minor версии отклонён при рендеринге
- [ ] Kubelet failure на worker → symlink reverted, scenario stops
- [ ] etcd snapshot создан перед upgrade
- [ ] Symlink switch через `file.symlink`
- [ ] Drain before kubelet switch, uncordon after health
- [ ] Overlay re-apply на masters после upgrade
- [ ] Addons re-apply на first master
- [ ] `test=True` показывает `upgrade plan`
- [ ] Pause image version table shared с containerd formula
