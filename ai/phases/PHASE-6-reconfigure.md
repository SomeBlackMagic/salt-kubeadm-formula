# Phase 6 — Reconfigure

## Цель

Изменение kubelet и control-plane настроек на живом кластере без пересоздания. Backup, apply, health check, automatic rollback.

## Зависимости

- Phase 1: config.sls (patches)
- Phase 2: healthy state, pick_source
- Phase 4: etcd checks

## Что реализуем

### 1. `_states/kubeadm.py` — дополнительные states

#### `kubeadm.kubelet_config_applied`
- **Apply** (1.25–1.29): `kubeadm upgrade node phase kubelet-config --patches {patches_dir}`
- **Apply** (1.30+): пишет drop-in файл в kubelet config dir
- **test=True**:
  1. Fetch `kubelet-config` ConfigMap (через `__salt__['http.query']` к apiserver или через kubectl)
  2. Загрузить node patch из файла
  3. Применить patch в Python (dict merge)
  4. Diff vs текущий `/var/lib/kubelet/config.yaml` (через `__salt__['file.read']`)
  5. Вернуть `result: None` с diff
- Версия определяется из pillar или из kubelet `--version`

#### `kubeadm.control_plane_applied`
- **Apply**: `kubeadm init phase control-plane all --config node.yaml --patches {patches_dir}`
- **test=True**:
  1. Рендерить manifests во temporary dir (если возможно на данной версии kubeadm)
  2. Diff vs текущие `/etc/kubernetes/manifests/*.yaml`
  3. Вернуть `result: None` с diff per manifest
  4. Если dry-run невозможен (open question #6): `result: None` с "would run"

### 2. `_runners/kubeadm.py` — дополнения

#### `diff(layer, transport)` — read-only
- Для `kubelet`: pillar vs `kubelet-config` ConfigMap
- Для `cluster`: pillar vs `kubeadm-config` ConfigMap
- Для `addons`: pillar vs addon ConfigMaps
- Возвращает unified diff string

#### `upload_config(source, kind, transport)` — MUTATING
- **test mode**: описание "would upload {kind} config"
- `kubeadm init phase upload-config {kind}` (kind = `kubeadm` | `kubelet`)
- Обновляет ConfigMap в кластере

### 3. `kubeadm/reconfigure.sls`

Один узел, вызывается напрямую или через orch.

```
1. Render patches и /etc/default/kubelet из merged pillar
   (file.managed states — уже есть из config.sls)

2. Backup:
   backup_kubelet_config:
     file.copy:
       - name: {backup_dir}/{timestamp}/kubelet-config.yaml
       - source: /var/lib/kubelet/config.yaml
       - makedirs: True

   backup_manifests:
     file.copy:
       - name: {backup_dir}/{timestamp}/manifests/
       - source: /etc/kubernetes/manifests/
       - makedirs: True

   # Cleanup old backups: keep last N
   cleanup_old_backups:
     module.run:
       - name: kubeadm_util.cleanup_backups
       - path: {backup_dir}
       - keep: {{ backup_keep }}

3. Kubelet reconfigure:
   kubelet_config_apply:
     kubeadm.kubelet_config_applied:
       - patches: {patches_dir}
       - onchanges:
         - file: kubeadm_patch_kubeletconfiguration

   kubelet_restart:
     service.running:
       - name: kubelet
       - watch:
         - kubeadm: kubelet_config_apply
         - file: kubelet_defaults  # /etc/default/kubelet

4. Control plane (masters only, когда есть CP patches):
   cp_config_render:
     file.managed:
       - name: {config_dir}/node.yaml
       # ClusterConfiguration из orchestrate pillar ИЛИ из kubeadm-config CM
       # + node overlay

   control_plane_apply:
     kubeadm.control_plane_applied:
       - config: {config_dir}/node.yaml
       - patches: {patches_dir}
       - onchanges:
         - file: kubeadm_patch_kube-apiserver
         - file: kubeadm_patch_kube-controller-manager
         - file: kubeadm_patch_kube-scheduler
         # kubelet restarts static pods automatically

5. Health checks (with retry):
   - kubelet active (service.status, не cmd.run)
   - Node status not worse than before
   - Mirror pod hash matches manifest (через http.query к apiserver)
   - Local /readyz (http.query)
   - etcd /health (http.query на 2381)

6. ROLLBACK (onfail):
   rollback_manifests:
     file.copy:
       - name: /etc/kubernetes/manifests/
       - source: {backup_dir}/{timestamp}/manifests/
       - onfail:
         - kubeadm: control_plane_apply

   rollback_kubelet:
     file.copy:
       - name: /var/lib/kubelet/config.yaml
       - source: {backup_dir}/{timestamp}/kubelet-config.yaml
       - onfail:
         - kubeadm: kubelet_config_apply

   rollback_restart:
     service.running:
       - name: kubelet
       - onfail:
         - kubeadm: kubelet_config_apply
         - kubeadm: control_plane_apply

   # State всё равно fails → orch останавливается
```

### 4. `orch/kubeadm/reconfigure-kubelet.sls`

```
1. diff kubelet pillar vs kubelet-config CM → event
2. upload_config kubelet (на source master)
3. Masters один за одним → kubeadm.reconfigure
4. Workers батчами → kubeadm.reconfigure
5. drain: false по умолчанию (fields that need drain в denylist)

Failure: node rolls back → orch stops
Event: "ConfigMap already updated; fix pillar and re-run, or revert"
```

### 5. `orch/kubeadm/reconfigure-controlplane.sls`

```
1. diff cluster pillar vs kubeadm-config CM → event
2. Masters один за одним:
   - Передать ClusterConfiguration из pillar через step pillar
   - Node overlay поверх
   - kubeadm.reconfigure
3. ТОЛЬКО если все masters succeed: upload_config kubeadm
   (failure на master 2 → old ConfigMap matches majority)

4. certSANs change:
   - Per master: delete apiserver.crt/.key
   - kubeadm init phase certs apiserver --config node.yaml
   - Restart apiserver (remove + restore manifest)
```

---

## TDD: тесты

### Файл: `test/unit/test_state_reconfigure.py`

```python
"""
Тесты для states kubelet_config_applied и control_plane_applied.
"""
import pytest
from unittest.mock import MagicMock


class TestKubeletConfigApplied:
    """State kubeadm.kubelet_config_applied."""

    def test_test_mode_shows_diff(self, state_mod):
        """test=True → diff kubelet config."""
        state_mod.__opts__["test"] = True
        # Mock: текущий config
        state_mod.__salt__["file.read"] = MagicMock(
            return_value="cgroupDriver: systemd\nmaxPods: 110\n"
        )
        # Mock: CM config + patch → результат
        state_mod.__salt__["http.query"] = MagicMock(return_value={
            "status": 200,
            "body": '{"data": {"kubelet": "cgroupDriver: systemd\\nmaxPods: 110\\n"}}'
        })
        result = state_mod.kubelet_config_applied(
            name="kubelet-config", patches="/etc/kubernetes/patches"
        )
        assert result["result"] is None
        # Diff должен быть в comment или changes
        assert "diff" in result["comment"].lower() or result.get("changes")

    def test_apply_runs_kubeadm(self, state_mod):
        """Apply → kubeadm upgrade node phase kubelet-config."""
        state_mod.__salt__["cmd.run_all"] = MagicMock(return_value={
            "retcode": 0, "stdout": "ok", "stderr": ""
        })
        result = state_mod.kubelet_config_applied(
            name="kubelet-config", patches="/etc/kubernetes/patches"
        )
        cmd_str = state_mod.__salt__["cmd.run_all"].call_args[0][0]
        assert "upgrade node" in cmd_str
        assert "kubelet-config" in cmd_str
        assert "--patches" in cmd_str

    def test_no_changes_detected(self, state_mod):
        """Нет разницы → no changes."""
        state_mod.__opts__["test"] = True
        state_mod.__salt__["file.read"] = MagicMock(
            return_value="cgroupDriver: systemd\nmaxPods: 110\n"
        )
        # Идентичный конфиг после merge
        state_mod._compute_desired_config = MagicMock(
            return_value={"cgroupDriver": "systemd", "maxPods": 110}
        )
        result = state_mod.kubelet_config_applied(
            name="kubelet-config", patches="/etc/kubernetes/patches"
        )
        # no diff → result True (уже в нужном состоянии)
        assert result["result"] is True
        assert result["changes"] == {}


class TestControlPlaneApplied:
    """State kubeadm.control_plane_applied."""

    def test_test_mode_shows_manifest_diffs(self, state_mod):
        """test=True → diffs per manifest."""
        state_mod.__opts__["test"] = True
        state_mod.__salt__["file.read"] = MagicMock(return_value="apiVersion: v1\n...")
        result = state_mod.control_plane_applied(
            name="cp-apply",
            config="/etc/kubernetes/kubeadm/node.yaml",
            patches="/etc/kubernetes/patches",
        )
        assert result["result"] is None

    def test_apply_runs_init_phase(self, state_mod):
        """Apply → kubeadm init phase control-plane all."""
        state_mod.__salt__["cmd.run_all"] = MagicMock(return_value={
            "retcode": 0, "stdout": "ok", "stderr": ""
        })
        result = state_mod.control_plane_applied(
            name="cp-apply",
            config="/etc/kubernetes/kubeadm/node.yaml",
            patches="/etc/kubernetes/patches",
        )
        cmd_str = state_mod.__salt__["cmd.run_all"].call_args[0][0]
        assert "init phase control-plane all" in cmd_str
        assert "--config" in cmd_str
        assert "--patches" in cmd_str


class TestReconfigureSLS:
    """kubeadm/reconfigure.sls."""

    def test_backup_before_apply(self, render_sls):
        """Backup создаётся ДО apply."""
        states = render_sls("kubeadm/reconfigure.sls", minimal_pillar())
        backup_idx = next(i for i, s in enumerate(states) if "backup" in s.get("name", "").lower())
        apply_idx = next(i for i, s in enumerate(states)
                        if "kubelet_config" in s.get("name", "").lower()
                        or "control_plane" in s.get("name", "").lower())
        assert backup_idx < apply_idx

    def test_rollback_on_failure(self, render_sls):
        """onfail → restore from backup."""
        states = render_sls("kubeadm/reconfigure.sls", minimal_pillar())
        rollback = [s for s in states if "onfail" in s]
        assert len(rollback) >= 1

    def test_health_checks_use_http_query(self, render_sls):
        """Health checks через http.query, не curl/cmd.run."""
        states = render_sls("kubeadm/reconfigure.sls", minimal_pillar())
        health_states = [s for s in states if "healthy" in s.get("name", "").lower()]
        # Не должно быть cmd.run с curl
        for s in states:
            if s.get("fun") == "cmd.run":
                assert "curl" not in str(s), "Use http.query instead of cmd.run curl"
```

### Файл: `test/unit/test_runner_diff.py`

```python
"""
Тесты для runner kubeadm.diff.
"""
import pytest
from unittest.mock import MagicMock


class TestDiff:
    """Runner kubeadm.diff."""

    def test_kubelet_diff(self, runner_mod):
        """Diff kubelet pillar vs ConfigMap."""
        runner_mod._get_configmap = MagicMock(return_value={
            "data": {"kubelet": "cgroupDriver: systemd\nmaxPods: 110\n"}
        })
        runner_mod.__opts__["pillar"] = {
            "kubeadm": {"kubelet": {"cgroupDriver": "systemd", "maxPods": 200}}
        }
        result = runner_mod.diff(layer="kubelet", transport="minion")
        assert "maxPods" in result

    def test_no_diff(self, runner_mod):
        """Идентичные значения → пустой diff."""
        runner_mod._get_configmap = MagicMock(return_value={
            "data": {"kubelet": "cgroupDriver: systemd\n"}
        })
        runner_mod.__opts__["pillar"] = {
            "kubeadm": {"kubelet": {"cgroupDriver": "systemd"}}
        }
        result = runner_mod.diff(layer="kubelet", transport="minion")
        assert result == "" or result is None


class TestUploadConfig:
    """Runner kubeadm.upload_config."""

    def test_test_mode(self, runner_mod):
        runner_mod.__opts__["test"] = True
        result = runner_mod.upload_config(
            source="master-1", kind="kubelet", transport="minion"
        )
        assert result["result"] is None

    def test_uploads_kubeadm_config(self, runner_mod):
        runner_mod._cmd = MagicMock(return_value={"master-1": ""})
        result = runner_mod.upload_config(
            source="master-1", kind="kubeadm", transport="minion"
        )
        cmd_calls = str(runner_mod._cmd.call_args_list)
        assert "upload-config" in cmd_calls
        assert "kubeadm" in cmd_calls
```

### Файл: `test/unit/test_orch_reconfigure.py`

```python
"""
Тесты для orch reconfigure scenarios.
"""
import pytest


class TestOrchReconfigureKubelet:
    """orch/kubeadm/reconfigure-kubelet.sls."""

    def test_upload_before_nodes(self, render_orch):
        """upload_config kubelet ДО apply на nodes."""
        states = render_orch("orch/kubeadm/reconfigure-kubelet.sls", orch_pillar())
        upload_idx = next(i for i, s in enumerate(states) if "upload_config" in str(s))
        first_node = next(i for i, s in enumerate(states) if "reconfigure" in str(s.get("sls", "")))
        assert upload_idx < first_node

    def test_masters_before_workers(self, render_orch):
        """Masters обрабатываются до workers."""
        states = render_orch("orch/kubeadm/reconfigure-kubelet.sls", orch_pillar())
        # Порядок: masters sequential → workers batch
        # Проверяется через targets в salt.state steps
        pass  # зависит от рендеринга


class TestOrchReconfigureControlplane:
    """orch/kubeadm/reconfigure-controlplane.sls."""

    def test_upload_config_after_all_succeed(self, render_orch):
        """upload_config kubeadm ПОСЛЕ всех masters."""
        states = render_orch("orch/kubeadm/reconfigure-controlplane.sls", orch_pillar())
        upload_idx = next(i for i, s in enumerate(states) if "upload_config" in str(s))
        last_master = max(i for i, s in enumerate(states) if "reconfigure" in str(s.get("sls", "")))
        assert upload_idx > last_master

    def test_pillar_config_passed_to_nodes(self, render_orch):
        """ClusterConfiguration передаётся через pillar, не из CM."""
        states = render_orch("orch/kubeadm/reconfigure-controlplane.sls", orch_pillar())
        node_steps = [s for s in states if "reconfigure" in str(s.get("sls", ""))]
        for step in node_steps:
            pillar_data = step.get("pillar", {})
            assert "kubeadm" in pillar_data or "cluster" in str(pillar_data)

    def test_certsans_change_reissues_cert(self, render_orch):
        """certSANs change → delete cert + re-issue + restart apiserver."""
        pillar = orch_pillar()
        pillar["kubeadm"]["orchestrate"]["_certsans_changed"] = True
        states = render_orch("orch/kubeadm/reconfigure-controlplane.sls", pillar)
        cert_steps = [s for s in states if "apiserver.crt" in str(s) or "certs apiserver" in str(s)]
        assert len(cert_steps) >= 1
```

---

## TDD: порядок работы

### Шаг 1 — Тесты
1. `test/unit/test_state_reconfigure.py`
2. `test/unit/test_runner_diff.py`
3. `test/unit/test_orch_reconfigure.py`

### Шаг 2 — Валидация
- Real diffs в test=True (не "would run")
- Backup before apply, rollback on failure
- Health checks через Salt modules, не cmd.run
- upload_config timing: kubelet before, kubeadm after

### Шаг 3 — Реализация
1. `_states/kubeadm.py`: `kubelet_config_applied`, `control_plane_applied`
2. `_runners/kubeadm.py`: `diff`, `upload_config`
3. `kubeadm/reconfigure.sls`
4. `orch/kubeadm/reconfigure-kubelet.sls`
5. `orch/kubeadm/reconfigure-controlplane.sls`

### Шаг 4 — Тестирование
1. Unit: `pytest test/unit/ -v`
2. `systemReserved` changed on one worker via `state.apply kubeadm.reconfigure`
3. apiserver resources changed on one master; survives `kubeadm upgrade node`
4. Broken apiserver flag → auto rollback, scenario stops
5. `test=True` shows kubelet config.yaml diff and manifest diffs

---

## Acceptance criteria

- [ ] `systemReserved` изменён на одном worker через `state.apply kubeadm.reconfigure`
- [ ] apiserver resources изменены на одном master; переживают `kubeadm upgrade node` (patches)
- [ ] Broken apiserver flag → автоматический rollback, scenario stops
- [ ] `test=True` показывает diff kubelet `config.yaml` и manifests
- [ ] Backup создаётся до apply; rollback восстанавливает из backup
- [ ] Health checks используют `http.query` и `service.status`
- [ ] reconfigure-kubelet: upload_config ДО nodes
- [ ] reconfigure-controlplane: upload_config ПОСЛЕ всех masters
- [ ] certSANs change → re-issue cert + restart apiserver
