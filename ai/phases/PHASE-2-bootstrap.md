# Phase 2 — Bootstrap

## Цель

Создание первого master-узла кластера. После этой фазы работает single-master Kubernetes с apiserver, etcd, controller-manager, scheduler.

## Зависимости

- Phase 0: `kubeadm_util.py`, `map.jinja`
- Phase 1: `binaries.sls`, `kubelet.sls`, `config.sls`

## Что реализуем

### 1. `_states/kubeadm.py` — state module

#### `kubeadm.initialized`
- **Apply**: `kubeadm init --config {config_dir}/init.yaml --upload-certs --patches {patches_dir}`
- **Guard**: `unless: test -f /etc/kubernetes/admin.conf` (init уже выполнен)
- **`managed_by: external`**: добавляет `--skip-phases=addon/kube-proxy` и/или `addon/coredns`
- **test=True**: возвращает `result: None`, описание что будет сделано, рендеренный конфиг; опционально `kubeadm init --dry-run`
- **НЕ cmd.run**: custom state вызывает `__salt__['cmd.run_all']` внутри Python с полным контролем над return

#### `kubeadm.healthy`
- **Apply**: проверки с retry (`retries`, `interval` из pillar)
  - `/readyz` через curl/urllib на localhost:6443
  - etcd `/health` на localhost:2381
  - kubelet active
- **test=True**: однократная проверка текущего состояния без retry, `result: None`
- Реализация через `__salt__['http.query']` для HTTP-проверок и `__salt__['service.status']` для kubelet — **не cmd.run для curl**

### 2. `_runners/kubeadm.py` — transport layer и read-only runners

#### Transport layer
```python
def _client(transport):
    """Возвращает LocalClient или SSHClient по параметру transport."""
    if transport == "minion":
        return salt.client.LocalClient()
    elif transport == "ssh":
        return salt.client.ssh.SSHClient()
    raise ValueError(f"Unknown transport: {transport}")

def _cmd(target, fun, arg=None, kwarg=None, transport="minion"):
    """Унифицированный вызов на узле через выбранный транспорт."""
    client = _client(transport)
    return client.cmd(target, fun, arg=arg, kwarg=kwarg)
```

#### `cluster_exists(nodegroup, transport)`
- Read-only, безопасен при render
- Проверяет `/readyz` через controlPlaneEndpoint (HTTP)
- Проверяет наличие `admin.conf` и `/etc/kubernetes/manifests/etcd.yaml` на каждом master
- Возвращает `True` если хотя бы один признак кластера найден
- Проверяет через **оба** канала: LB может быть down, но кластер существует

#### `pick_init_node(nodegroup, source_ip)`
- Read-only
- `source_ip` если задан, иначе первый хост из nodegroup
- Возвращает minion ID

#### `pick_source(nodegroup, transport, source_ip=None)`
- Read-only
- Первый master, который: `/readyz` OK, etcd healthy, node Ready, `admin.conf` не expired
- Используется как source для cluster-level операций

### 3. `orch/kubeadm/bootstrap.sls`

Порядок шагов:

```yaml
# 1. Guard: кластер НЕ должен существовать
bootstrap_guard:
  salt.runner:
    - name: kubeadm.cluster_exists
    - nodegroup: {{ masters_nodegroup }}
    - transport: {{ transport }}
    # Ожидаем False; True → fail

# 2. Endpoint precheck: controlPlaneEndpoint резолвится, порт доступен
endpoint_precheck:
  salt.runner:
    - name: kubeadm.endpoint_precheck
    - endpoint: {{ controlPlaneEndpoint }}

# 3. Init node selection
# (Jinja: init_node = pick_init_node(...))

# 4. Containerd formula
containerd_on_init:
  salt.state:
    - tgt: {{ init_node }}
    - sls: containerd
    {{ ssh_kwarg }}

# 5. Node states (binaries, kubelet, config)
node_states_init:
  salt.state:
    - tgt: {{ init_node }}
    - sls: kubeadm
    {{ ssh_kwarg }}

# 6. Preflight
preflight:
  salt.state:
    - tgt: {{ init_node }}
    - sls: kubeadm.init_cluster
    - pillar:
        kubeadm:
          _phase: preflight
    {{ ssh_kwarg }}

# 7. Init
init_cluster:
  salt.state:
    - tgt: {{ init_node }}
    - sls: kubeadm.init_cluster
    - pillar:
        kubeadm:
          _phase: init
    {{ ssh_kwarg }}

# 8. Health check
init_health:
  salt.state:
    - tgt: {{ init_node }}
    - sls: kubeadm.init_cluster
    - pillar:
        kubeadm:
          _phase: health
    {{ ssh_kwarg }}

# 9. Event
bootstrap_done:
  salt.runner:
    - name: event.send
    - tag: kubeadm/bootstrap/done/{{ init_node }}
```

**Transport-agnostic:**
```jinja
{%- set transport = salt['pillar.get']('kubeadm:orchestrate:transport', 'minion') %}
{%- set ssh_kwarg = 'ssh: True' if transport == 'ssh' else '' %}
```

Каждый `salt.state` шаг передаёт `test: {{ opts['test'] }}` явно.

### 4. `kubeadm/init_cluster.sls`

Используется только из orch bootstrap, не из highstate.

```yaml
# Preflight phase
{%- if phase == 'preflight' %}
kubeadm_preflight:
  kubeadm.preflight:
    - config: {{ config_dir }}/init.yaml
    - pull_images: True

# Init phase
{%- elif phase == 'init' %}
kubeadm_init:
  kubeadm.initialized:
    - config: {{ config_dir }}/init.yaml
    - patches: {{ patches_dir }}
    - upload_certs: True
    - skip_phases: {{ skip_phases }}

# Health phase
{%- elif phase == 'health' %}
kubeadm_health:
  kubeadm.healthy:
    - checks:
      - readyz
      - etcd
      # НЕ node_ready: нет CNI
    - retries: {{ health.retries }}
    - interval: {{ health.interval }}
{%- endif %}
```

### 5. `kubeadm/reset.sls`

```yaml
{%- if salt['pillar.get']('kubeadm:confirm_reset', False) %}
kubeadm_reset:
  cmd.run:
    - name: kubeadm reset -f
    - onlyif: test -f /etc/kubernetes/admin.conf -o -f /etc/kubernetes/kubelet.conf

kubeadm_cleanup:
  file.absent:
    - names:
      - /etc/kubernetes
      - /var/lib/etcd
      - /etc/cni/net.d
    - require:
      - cmd: kubeadm_reset

# iptables/ipvs cleanup через module.run
kubeadm_iptables_cleanup:
  module.run:
    - name: iptables.flush
    - require:
      - cmd: kubeadm_reset
{%- else %}
reset_refused:
  test.fail_without_changes:
    - comment: "kubeadm reset requires confirm_reset: true in pillar"
{%- endif %}
```

> Примечание: `cmd.run` для `kubeadm reset` — одно из немногих мест где избежать его нельзя, т.к. kubeadm — внешний бинарник. Но он обёрнут в custom state (`kubeadm.initialized` guard) и не используется для файловых операций.

---

## TDD: тесты

### Файл: `test/unit/test_state_kubeadm.py`

```python
"""
Тесты для _states/kubeadm.py.

Используем mock для __salt__ и __opts__.
"""
import pytest
from unittest.mock import MagicMock, patch


class TestKubeadmInitialized:
    """State kubeadm.initialized."""

    def test_already_initialized_no_changes(self, state_mod):
        """Если admin.conf существует — no changes."""
        state_mod.__salt__["file.file_exists"] = MagicMock(return_value=True)
        result = state_mod.initialized(
            name="init", config="/etc/kubernetes/kubeadm/init.yaml"
        )
        assert result["result"] is True
        assert result["changes"] == {}
        assert "already initialized" in result["comment"].lower()

    def test_init_runs_kubeadm(self, state_mod):
        """Если admin.conf нет — запускает kubeadm init."""
        state_mod.__salt__["file.file_exists"] = MagicMock(return_value=False)
        state_mod.__salt__["cmd.run_all"] = MagicMock(return_value={
            "retcode": 0, "stdout": "initialized", "stderr": ""
        })
        result = state_mod.initialized(
            name="init",
            config="/etc/kubernetes/kubeadm/init.yaml",
            patches="/etc/kubernetes/patches",
        )
        assert result["result"] is True
        assert result["changes"] != {}
        # Проверяем что вызван kubeadm init с правильными аргументами
        cmd_call = state_mod.__salt__["cmd.run_all"].call_args
        cmd_str = cmd_call[0][0] if cmd_call[0] else cmd_call[1].get("cmd", "")
        assert "kubeadm init" in cmd_str
        assert "--config" in cmd_str
        assert "--patches" in cmd_str
        assert "--upload-certs" in cmd_str

    def test_init_test_mode_no_changes(self, state_mod):
        """test=True: не запускает init, возвращает result=None."""
        state_mod.__opts__["test"] = True
        state_mod.__salt__["file.file_exists"] = MagicMock(return_value=False)
        result = state_mod.initialized(
            name="init", config="/etc/kubernetes/kubeadm/init.yaml"
        )
        assert result["result"] is None
        assert "would" in result["comment"].lower()
        state_mod.__salt__["cmd.run_all"].assert_not_called()

    def test_init_failure_returns_false(self, state_mod):
        """kubeadm init failure → result=False."""
        state_mod.__salt__["file.file_exists"] = MagicMock(return_value=False)
        state_mod.__salt__["cmd.run_all"] = MagicMock(return_value={
            "retcode": 1, "stdout": "", "stderr": "error: port 6443 in use"
        })
        result = state_mod.initialized(
            name="init", config="/etc/kubernetes/kubeadm/init.yaml"
        )
        assert result["result"] is False
        assert "error" in result["comment"].lower()

    def test_skip_phases_external_proxy(self, state_mod):
        """managed_by: external для kube-proxy → --skip-phases=addon/kube-proxy."""
        state_mod.__salt__["file.file_exists"] = MagicMock(return_value=False)
        state_mod.__salt__["cmd.run_all"] = MagicMock(return_value={
            "retcode": 0, "stdout": "ok", "stderr": ""
        })
        result = state_mod.initialized(
            name="init",
            config="/etc/kubernetes/kubeadm/init.yaml",
            skip_phases=["addon/kube-proxy"],
        )
        cmd_str = state_mod.__salt__["cmd.run_all"].call_args[0][0]
        assert "--skip-phases=addon/kube-proxy" in cmd_str


class TestKubeadmHealthy:
    """State kubeadm.healthy."""

    def test_all_checks_pass(self, state_mod):
        """Все проверки проходят → result=True."""
        state_mod.__salt__["http.query"] = MagicMock(return_value={
            "status": 200, "body": "ok"
        })
        state_mod.__salt__["service.status"] = MagicMock(return_value=True)
        result = state_mod.healthy(
            name="health", checks=["readyz", "etcd", "kubelet"]
        )
        assert result["result"] is True

    def test_readyz_fails(self, state_mod):
        """apiserver /readyz fail → result=False."""
        state_mod.__salt__["http.query"] = MagicMock(return_value={
            "status": 500, "body": "not ready"
        })
        result = state_mod.healthy(
            name="health", checks=["readyz"], retries=1, interval=0
        )
        assert result["result"] is False

    def test_test_mode_single_check(self, state_mod):
        """test=True: одна проверка без retry."""
        state_mod.__opts__["test"] = True
        state_mod.__salt__["http.query"] = MagicMock(return_value={
            "status": 200, "body": "ok"
        })
        result = state_mod.healthy(
            name="health", checks=["readyz"], retries=30
        )
        assert result["result"] is None
        # http.query вызван ровно 1 раз (нет retry в test mode)
        assert state_mod.__salt__["http.query"].call_count == 1

    def test_kubelet_check_uses_service_status(self, state_mod):
        """Проверка kubelet через service.status, не cmd.run."""
        state_mod.__salt__["service.status"] = MagicMock(return_value=True)
        state_mod.__salt__["http.query"] = MagicMock(return_value={"status": 200})
        state_mod.healthy(name="health", checks=["kubelet"])
        state_mod.__salt__["service.status"].assert_called_with("kubelet")


class TestKubeadmReset:
    """kubeadm.reset.sls."""

    def test_refuses_without_confirm(self, render_sls):
        """Без confirm_reset — отказ."""
        pillar = minimal_pillar()
        # confirm_reset не задан
        states = render_sls("kubeadm/reset.sls", pillar)
        fail_states = [s for s in states if "fail_without_changes" in s.get("fun", "")]
        assert len(fail_states) >= 1

    def test_runs_with_confirm(self, render_sls):
        """С confirm_reset: true — выполняет reset."""
        pillar = minimal_pillar()
        pillar["kubeadm"]["confirm_reset"] = True
        states = render_sls("kubeadm/reset.sls", pillar)
        reset_states = [s for s in states if "kubeadm reset" in str(s)]
        assert len(reset_states) >= 1
```

### Файл: `test/unit/test_runner_kubeadm.py`

```python
"""
Тесты для _runners/kubeadm.py.
"""
import pytest
from unittest.mock import MagicMock, patch


class TestClusterExists:
    """Runner kubeadm.cluster_exists."""

    def test_no_cluster(self, runner_mod):
        """Ни один признак кластера не найден → False."""
        runner_mod._cmd = MagicMock(return_value={
            "master-1": {"retcode": 1}  # admin.conf не существует
        })
        runner_mod._http_check = MagicMock(return_value=False)  # /readyz fail
        result = runner_mod.cluster_exists(nodegroup="k8s-masters", transport="minion")
        assert result is False

    def test_cluster_via_readyz(self, runner_mod):
        """controlPlaneEndpoint /readyz 200 → True."""
        runner_mod._http_check = MagicMock(return_value=True)
        result = runner_mod.cluster_exists(nodegroup="k8s-masters", transport="minion")
        assert result is True

    def test_cluster_via_admin_conf(self, runner_mod):
        """admin.conf существует на master → True (даже если LB down)."""
        runner_mod._http_check = MagicMock(return_value=False)  # LB down
        runner_mod._cmd = MagicMock(return_value={
            "master-1": True  # file.file_exists → True
        })
        result = runner_mod.cluster_exists(nodegroup="k8s-masters", transport="minion")
        assert result is True


class TestPickSource:
    """Runner kubeadm.pick_source."""

    def test_picks_first_healthy(self, runner_mod):
        """Выбирает первый healthy master."""
        runner_mod._check_master_health = MagicMock(
            side_effect=[False, True]  # master-1 unhealthy, master-2 OK
        )
        runner_mod._nodegroup_members = MagicMock(
            return_value=["master-1", "master-2", "master-3"]
        )
        result = runner_mod.pick_source(nodegroup="k8s-masters", transport="minion")
        assert result == "master-2"

    def test_source_ip_override(self, runner_mod):
        """source_ip задан — используется вместо автовыбора."""
        runner_mod._check_master_health = MagicMock(return_value=True)
        result = runner_mod.pick_source(
            nodegroup="k8s-masters", transport="minion", source_ip="master-3"
        )
        assert result == "master-3"

    def test_no_healthy_master_raises(self, runner_mod):
        """Нет healthy master → исключение."""
        runner_mod._check_master_health = MagicMock(return_value=False)
        runner_mod._nodegroup_members = MagicMock(
            return_value=["master-1", "master-2"]
        )
        with pytest.raises(Exception, match="[Nn]o healthy"):
            runner_mod.pick_source(nodegroup="k8s-masters", transport="minion")
```

### Файл: `test/unit/test_orch_bootstrap.py`

```python
"""
Тесты рендеринга orch/kubeadm/bootstrap.sls.
"""
import pytest


class TestOrchBootstrap:
    """Orchestrate bootstrap: структура и параметры."""

    def test_cluster_exists_guard_first(self, render_orch):
        """Первый шаг — проверка cluster_exists."""
        states = render_orch("orch/kubeadm/bootstrap.sls", orch_pillar())
        first_runner = next(s for s in states if "runner" in s.get("fun", ""))
        assert "cluster_exists" in str(first_runner)

    def test_test_passed_to_nested_states(self, render_orch):
        """test: True передаётся во все salt.state шаги."""
        states = render_orch("orch/kubeadm/bootstrap.sls", orch_pillar(), test=True)
        state_steps = [s for s in states if s.get("fun") == "salt.state"]
        for step in state_steps:
            assert step.get("kwarg", {}).get("test") is True or \
                   step.get("pillar", {}).get("test") is True

    def test_ssh_transport_sets_ssh_flag(self, render_orch):
        """transport: ssh → все salt.state имеют ssh: True."""
        pillar = orch_pillar(transport="ssh")
        states = render_orch("orch/kubeadm/bootstrap.sls", pillar)
        state_steps = [s for s in states if s.get("fun") == "salt.state"]
        for step in state_steps:
            assert step.get("kwarg", {}).get("ssh") is True

    def test_event_emitted(self, render_orch):
        """После init — event kubeadm/bootstrap/done/<node>."""
        states = render_orch("orch/kubeadm/bootstrap.sls", orch_pillar())
        events = [s for s in states if "event.send" in str(s)]
        assert any("bootstrap/done" in str(e) for e in events)

    def test_skip_phases_external_proxy(self, render_orch):
        """managed_by: external → skip-phases в init."""
        pillar = orch_pillar()
        pillar["kubeadm"]["addons"]["kube_proxy"]["managed_by"] = "external"
        states = render_orch("orch/kubeadm/bootstrap.sls", pillar)
        # skip_phases должен содержать addon/kube-proxy
        init_step = next(s for s in states if "init_cluster" in str(s.get("sls", "")))
        skip = init_step.get("pillar", {}).get("kubeadm", {}).get("_skip_phases", [])
        assert "addon/kube-proxy" in skip
```

---

## TDD: порядок работы

### Шаг 1 — Пишем тесты
1. `test/unit/test_state_kubeadm.py` — state module тесты
2. `test/unit/test_runner_kubeadm.py` — runner тесты
3. `test/unit/test_orch_bootstrap.py` — orch рендеринг
4. `test/unit/conftest.py` — фикстуры `state_mod`, `runner_mod`, `render_orch`

### Шаг 2 — Валидация тестов
1. Guard: cluster_exists → True = fail
2. Init: admin.conf guard, skip-phases, test mode
3. Health: http.query (не curl), service.status (не cmd.run)
4. Transport: ssh flag в salt.state шагах
5. Events: emitted after each step

### Шаг 3 — Реализация
1. `_states/kubeadm.py`: `initialized`, `healthy`
2. `_runners/kubeadm.py`: transport layer, `cluster_exists`, `pick_init_node`, `pick_source`
3. `kubeadm/init_cluster.sls`
4. `kubeadm/reset.sls`
5. `orch/kubeadm/bootstrap.sls`

### Шаг 4 — Прогон тестов
1. Unit: `pytest test/unit/ -v`
2. Integration: `salt-run state.orchestrate orch.kubeadm.bootstrap test=True` — ничего не создано
3. Integration: bootstrap на чистой VM → single master, `/readyz` 200
4. Повторный bootstrap → refused ("cluster exists")

---

## Acceptance criteria

- [ ] Single-master кластер создан через minion и через salt-ssh
- [ ] Повторный bootstrap отклонён (cluster exists)
- [ ] Bootstrap отклонён при недоступном LB (endpoint precheck)
- [ ] `test=True` ничего не создаёт; показывает рендеренный конфиг
- [ ] Events видны в `salt-run state.event`
- [ ] Health checks используют `http.query` и `service.status`, не `cmd.run curl`
- [ ] `kubeadm.reset` работает только с `confirm_reset: true`
- [ ] `managed_by: external` для kube-proxy → `--skip-phases=addon/kube-proxy`
