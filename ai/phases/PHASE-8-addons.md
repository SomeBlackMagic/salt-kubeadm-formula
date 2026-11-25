# Phase 8 — Addons

## Цель

Управление kube-proxy и CoreDNS конфигурацией из pillar. ConfigMap updates, DaemonSet/Deployment patches, rollout management.

## Зависимости

- Phase 2: pick_source, healthy
- Phase 6: diff runner

## Что реализуем

### 1. `_states/kubeadm.py` — дополнение

#### `kubeadm.addon_applied`
- **Apply**:
  - ConfigMap update (через kubeadm phase для kube-proxy, через kubectl для CoreDNS)
  - Workload patch via server-side apply (`--field-manager=salt-kubeadm`)
  - Rollout restart (kube-proxy) или no restart (CoreDNS с `reload` plugin)
  - Rollout status с timeout
- **test=True**:
  - ConfigMap diff (pillar vs current)
  - `kubectl diff` (server-side dry-run) для workload
  - `result: None` с diffs

### 2. `kubeadm/addons.sls`

Targeted by orch at ONE healthy master, never at all masters.

#### kube-proxy (`managed_by: kubeadm`):

```yaml
{%- if addons.kube_proxy.managed_by == 'kubeadm' %}

# 1. Backup
kube_proxy_backup:
  kubeadm.addon_backup:
    - name: kube-proxy
    - backup_dir: {{ backup_dir }}/addons/{{ timestamp }}

# 2. KubeProxyConfiguration → ConfigMap
kube_proxy_config:
  kubeadm.addon_applied:
    - name: kube-proxy-config
    - addon: kube-proxy
    - phase: config
    # kubeadm init phase addon kube-proxy --config ...
    # Сохраняет kubeadm format → ConfigMap переживёт upgrade

# 3. Rollout restart (после config change)
kube_proxy_rollout:
  kubeadm.addon_applied:
    - name: kube-proxy-rollout
    - addon: kube-proxy
    - phase: rollout
    - timeout: 300
    - onchanges:
      - kubeadm: kube_proxy_config

# 4. DaemonSet patch (SSA)
kube_proxy_daemonset:
  kubeadm.addon_applied:
    - name: kube-proxy-daemonset
    - addon: kube-proxy
    - phase: patch
    - field_manager: salt-kubeadm
    # kubectl apply --server-side --field-manager=salt-kubeadm

# 5. Warning: mode switch iptables ↔ ipvs
{%- if addons.kube_proxy.config.get('mode') %}
kube_proxy_mode_warning:
  test.configurable_test_state:
    - result: True
    - comment: >
        WARNING: Switching kube-proxy mode leaves old rules on nodes.
        Ensure ipvs kernel modules are loaded (containerd formula).
    - onchanges:
      - kubeadm: kube_proxy_config
{%- endif %}

{%- endif %}
```

#### CoreDNS (`managed_by: kubeadm`):

```yaml
{%- if addons.coredns.managed_by == 'kubeadm' %}

# 1. Backup
coredns_backup:
  kubeadm.addon_backup:
    - name: coredns
    - backup_dir: {{ backup_dir }}/addons/{{ timestamp }}

# 2. Corefile → ConfigMap coredns
coredns_corefile:
  kubeadm.addon_applied:
    - name: coredns-corefile
    - addon: coredns
    - phase: config
    # Raw Corefile text → ConfigMap
    # No restart: reload plugin picks it up

# 3. Deployment patch (SSA)
coredns_deployment:
  kubeadm.addon_applied:
    - name: coredns-deployment
    - addon: coredns
    - phase: patch
    - field_manager: salt-kubeadm
    # kubectl apply --server-side --field-manager=salt-kubeadm
    # Then rollout status

# 4. DNS check (optional)
coredns_dns_check:
  kubeadm.healthy:
    - checks:
      - dns_query
    - query: kubernetes.default.svc.cluster.local
    - dns_service_ip: {{ kubelet.clusterDNS[0] }}
    - retries: {{ health.retries }}
    - interval: {{ health.interval }}

{%- endif %}
```

#### `managed_by: external`:

```yaml
{%- if addons.kube_proxy.managed_by == 'external' %}
# Ничего не делаем. --skip-phases при init (Phase 2).
# Не трогается addons и upgrade.
kube_proxy_external:
  test.nop:
    - comment: "kube-proxy managed externally, skipped"
{%- endif %}
```

### 3. Rollback logic (inside `addon_applied` state)

```python
def addon_applied(name, addon, phase, **kwargs):
    # Before apply: backup ConfigMap/DaemonSet/Deployment to backup_dir
    # Apply
    # If apply fails:
    #   - Restore ConfigMap from backup
    #   - kubectl rollout undo ds/kube-proxy (or deployment/coredns)
    #   - Return result=False
```

### 4. `orch/kubeadm/addons.sls`

```
1. pick_source → one healthy master
2. salt.state kubeadm.addons на source
3. Events per addon
```

---

## TDD: тесты

### Файл: `test/unit/test_state_addon.py`

```python
"""
Тесты для state kubeadm.addon_applied.
"""
import pytest
from unittest.mock import MagicMock


class TestAddonApplied:
    """State kubeadm.addon_applied."""

    def test_test_mode_configmap_diff(self, state_mod):
        """test=True → ConfigMap diff."""
        state_mod.__opts__["test"] = True
        state_mod.__salt__["cmd.run_all"] = MagicMock(return_value={
            "retcode": 0, "stdout": "diff output here", "stderr": ""
        })
        result = state_mod.addon_applied(
            name="kube-proxy-config", addon="kube-proxy", phase="config"
        )
        assert result["result"] is None
        # kubectl diff вызван
        cmd_calls = [str(c) for c in state_mod.__salt__["cmd.run_all"].call_args_list]
        assert any("diff" in c for c in cmd_calls)

    def test_kube_proxy_uses_kubeadm_phase(self, state_mod):
        """kube-proxy config → kubeadm init phase addon kube-proxy."""
        state_mod.__salt__["cmd.run_all"] = MagicMock(return_value={
            "retcode": 0, "stdout": "ok", "stderr": ""
        })
        state_mod.addon_applied(
            name="kube-proxy-config", addon="kube-proxy", phase="config"
        )
        cmd_str = str(state_mod.__salt__["cmd.run_all"].call_args_list)
        assert "init phase addon kube-proxy" in cmd_str

    def test_ssa_field_manager(self, state_mod):
        """SSA patch uses --field-manager=salt-kubeadm."""
        state_mod.__salt__["cmd.run_all"] = MagicMock(return_value={
            "retcode": 0, "stdout": "ok", "stderr": ""
        })
        state_mod.addon_applied(
            name="kube-proxy-ds", addon="kube-proxy",
            phase="patch", field_manager="salt-kubeadm",
        )
        cmd_str = str(state_mod.__salt__["cmd.run_all"].call_args_list)
        assert "--server-side" in cmd_str
        assert "--field-manager=salt-kubeadm" in cmd_str

    def test_rollout_restart_on_config_change(self, state_mod):
        """Config change → rollout restart."""
        state_mod.__salt__["cmd.run_all"] = MagicMock(return_value={
            "retcode": 0, "stdout": "ok", "stderr": ""
        })
        state_mod.addon_applied(
            name="kube-proxy-rollout", addon="kube-proxy", phase="rollout"
        )
        cmd_str = str(state_mod.__salt__["cmd.run_all"].call_args_list)
        assert "rollout restart" in cmd_str

    def test_coredns_no_restart(self, state_mod):
        """CoreDNS Corefile change: no restart (reload plugin)."""
        state_mod.__salt__["cmd.run_all"] = MagicMock(return_value={
            "retcode": 0, "stdout": "ok", "stderr": ""
        })
        state_mod.addon_applied(
            name="coredns-corefile", addon="coredns", phase="config"
        )
        cmd_str = str(state_mod.__salt__["cmd.run_all"].call_args_list)
        assert "rollout restart" not in cmd_str

    def test_rollback_on_failure(self, state_mod):
        """Apply failure → restore + rollout undo."""
        state_mod.__salt__["cmd.run_all"] = MagicMock(side_effect=[
            {"retcode": 1, "stdout": "", "stderr": "error"},  # apply fails
            {"retcode": 0, "stdout": "", "stderr": ""},  # restore
            {"retcode": 0, "stdout": "", "stderr": ""},  # rollout undo
        ])
        result = state_mod.addon_applied(
            name="kube-proxy-config", addon="kube-proxy", phase="config"
        )
        assert result["result"] is False
        # rollout undo called
        cmd_calls = [str(c) for c in state_mod.__salt__["cmd.run_all"].call_args_list]
        assert any("rollout undo" in c or "restore" in c.lower() for c in cmd_calls)


class TestAddonDNSCheck:
    """DNS query check для CoreDNS."""

    def test_dns_query_check(self, state_mod):
        """DNS query проверка через salt module."""
        state_mod.__salt__["dnsutil.A"] = MagicMock(return_value=["10.96.0.1"])
        result = state_mod.healthy(
            name="dns", checks=["dns_query"],
            query="kubernetes.default.svc.cluster.local",
            dns_service_ip="10.96.0.10",
        )
        assert result["result"] is True


class TestManagedByExternal:
    """managed_by: external → skip."""

    def test_external_kube_proxy_skipped(self, render_sls):
        """managed_by: external → ничего не делаем."""
        pillar = minimal_pillar()
        pillar["kubeadm"]["addons"]["kube_proxy"]["managed_by"] = "external"
        states = render_sls("kubeadm/addons.sls", pillar)
        addon_states = [s for s in states
                        if "kube-proxy" in str(s) and s.get("fun") != "test.nop"]
        assert len(addon_states) == 0

    def test_external_coredns_skipped(self, render_sls):
        """managed_by: external → ничего."""
        pillar = minimal_pillar()
        pillar["kubeadm"]["addons"]["coredns"]["managed_by"] = "external"
        states = render_sls("kubeadm/addons.sls", pillar)
        addon_states = [s for s in states
                        if "coredns" in str(s) and s.get("fun") != "test.nop"]
        assert len(addon_states) == 0
```

### Файл: `test/unit/test_orch_addons.py`

```python
"""
Тесты для orch/kubeadm/addons.sls.
"""
import pytest


class TestOrchAddons:
    """Orchestrate addons."""

    def test_runs_on_single_master(self, render_orch):
        """Addons применяются на ОДНОМ master (pick_source)."""
        states = render_orch("orch/kubeadm/addons.sls", orch_pillar())
        state_steps = [s for s in states if s.get("fun") == "salt.state"]
        # Все salt.state шаги должны быть на одном target
        targets = {s["tgt"] for s in state_steps}
        assert len(targets) == 1

    def test_event_per_addon(self, render_orch):
        """Event после каждого addon."""
        states = render_orch("orch/kubeadm/addons.sls", orch_pillar())
        events = [s for s in states if "event.send" in str(s)]
        assert len(events) >= 1
```

---

## TDD: порядок работы

### Шаг 1 — Тесты
1. `test/unit/test_state_addon.py`
2. `test/unit/test_orch_addons.py`

### Шаг 2 — Валидация
- kubeadm phase для kube-proxy (сохраняет формат для upgrade)
- SSA с field-manager
- CoreDNS: no restart (reload plugin)
- Rollback: restore + rollout undo
- external → skip
- kubectl diff для test=True

### Шаг 3 — Реализация
1. `_states/kubeadm.py`: `addon_applied`, `addon_backup`
2. `kubeadm/addons.sls`
3. `orch/kubeadm/addons.sls`

### Шаг 4 — Тестирование
1. Unit: `pytest test/unit/ -v`
2. Corefile change applied без restart; replicas и resources changed
3. Failed rollout restored automatically
4. external addon не тронут

---

## Acceptance criteria

- [ ] Corefile change applied без restart (reload plugin); replicas и resources changed
- [ ] Failed rollout → automatic restore + rollout undo
- [ ] `external` addon не тронут ни addons ни upgrade
- [ ] kube-proxy config через kubeadm phase (переживает upgrade)
- [ ] SSA с `--field-manager=salt-kubeadm`
- [ ] DNS query check после CoreDNS update
- [ ] mode switch iptables ↔ ipvs → warning
- [ ] Backup перед apply; restore on failure
- [ ] `test=True` показывает ConfigMap diff и kubectl diff
- [ ] Addons apply на одном master (pick_source)
