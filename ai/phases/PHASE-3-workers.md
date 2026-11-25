# Phase 3 — Workers

## Цель

Присоединение worker-узлов к кластеру. Labels, taints, batch join, failure handling.

## Зависимости

- Phase 2: `_states/kubeadm.py` (initialized, healthy), `_runners/kubeadm.py` (transport, pick_source), orch bootstrap

## Что реализуем

### 1. `_runners/kubeadm.py` — дополнительные функции

#### `join_node(node, role, source, transport)` — MUTATING
- **test mode**: `__opts__['test']` → возвращает `result: None`, описание "would join {node} as {role} via {source}"
- Генерирует fresh token (TTL 15m) на source: `kubeadm token create --ttl 15m`
- Получает CA hash: `openssl x509 -pubkey -in /etc/kubernetes/pki/ca.crt | openssl rsa -pubin -outform der | openssl dgst -sha256`
- Для control-plane: `kubeadm init phase upload-certs --upload-certs` → certificate key
- Передаёт token, hash, cert_key через `pillar:` в `salt.state` на target
- Запускает `kubeadm.join` state на target
- Token и cert_key никогда не попадают в command line, mine или events

#### `nodes(source, transport)` — read-only
- `kubectl get nodes -o json` на source
- Возвращает dict: `{node_name: {status, roles, version, labels, ...}}`

#### `pending_nodes(nodegroup, transport)` — read-only
- Members nodegroup минус nodes в кластере минус decommissioned
- Возвращает list minion IDs

#### `node_meta(source, node, labels, taints, transport)` — MUTATING
- **test mode**: diff текущих vs желаемых labels/taints
- Применяет labels через `kubectl label`
- Применяет taints через `kubectl taint`
- Записывает annotation `kubeadm.salt/managed-labels` с текущими managed labels
- При удалении label из pillar: удаляет label если он в managed-labels annotation
- Foreign labels (не в annotation) не трогает

### 2. `_states/kubeadm.py` — дополнение

#### `kubeadm.joined`
- **Guard**: `unless: test -f /etc/kubernetes/kubelet.conf` (join уже выполнен)
- **Apply**: рендерит join config в `{config_dir}/join.yaml` (mode 0600, `show_changes: False`), запускает `kubeadm join --config ... --patches ...`, удаляет join.yaml
- **test=True**: `result: None`, описание с какого source и каким способом
- Token file: 0600, deleted after use, не в diff output

### 3. `orch/kubeadm/join-worker.sls`

```
1. Targets: pillar targets ИЛИ pending_nodes(workers_nodegroup) минус decommissioned
2. Precheck: apiserver healthy via pick_source
   - Existing Node с тем же именем → fail ("use remove-node first")
   - replace: true → удалить old Node если NotReady
3. Разбивка на batches: targets | batch(batch_size)
4. Для каждого batch:
   a. Nested orch join-worker-batch с fresh token
   b. require от предыдущего batch (fail_fast: true по умолчанию)
```

### 4. `orch/kubeadm/join-worker-batch.sls`

```
Для каждого worker в batch:
1. Containerd formula
2. Node states (binaries, kubelet, config)
3. Join via runner join_node (fresh token на batch)
4. Postcheck: kubelet active, kubelet.conf present, Node registered
   - wait_ready: true → ждём Ready (когда CNI есть)
5. Labels и taints через node_meta с master
6. Event kubeadm/join-worker/done/<node>

Failure:
- kubeadm reset на failed worker
- kubectl delete node если Node был зарегистрирован
- keep_failed: true → оставить для debug
- fail_fast → следующий batch НЕ стартует
- max_fail: N → runner считает failures, продолжает до N
```

---

## TDD: тесты

### Файл: `test/unit/test_runner_join.py`

```python
"""
Тесты для runner kubeadm.join_node и связанных функций.
"""
import pytest
from unittest.mock import MagicMock, patch, call


class TestJoinNode:
    """Runner kubeadm.join_node."""

    def test_test_mode_no_changes(self, runner_mod):
        """test=True → описание, без реальных действий."""
        runner_mod.__opts__["test"] = True
        result = runner_mod.join_node(
            node="worker-1", role="worker",
            source="master-1", transport="minion",
        )
        assert result["result"] is None
        assert "would join" in result["comment"].lower()

    def test_generates_fresh_token(self, runner_mod):
        """Генерирует token с TTL 15m."""
        runner_mod._cmd = MagicMock(return_value={
            "master-1": "abcdef.0123456789abcdef"
        })
        runner_mod._apply_state = MagicMock(return_value={"result": True})
        runner_mod.join_node(
            node="worker-1", role="worker",
            source="master-1", transport="minion",
        )
        # Проверяем вызов token create
        token_call = [c for c in runner_mod._cmd.call_args_list
                      if "token create" in str(c)]
        assert len(token_call) >= 1
        assert "--ttl 15m" in str(token_call[0])

    def test_token_passed_via_pillar(self, runner_mod):
        """Token передаётся через pillar, не через command line на target."""
        runner_mod._cmd = MagicMock(return_value={"master-1": "token123"})
        runner_mod._apply_state = MagicMock(return_value={"result": True})
        runner_mod.join_node(
            node="worker-1", role="worker",
            source="master-1", transport="minion",
        )
        apply_call = runner_mod._apply_state.call_args
        pillar_data = apply_call[1].get("pillar", {}) or apply_call[0][2] if len(apply_call[0]) > 2 else {}
        # Token должен быть в pillar, не в cmd args
        assert "token" in str(pillar_data).lower()

    def test_no_cert_key_for_worker(self, runner_mod):
        """Worker join не запрашивает certificate key."""
        runner_mod._cmd = MagicMock(return_value={"master-1": "token123"})
        runner_mod._apply_state = MagicMock(return_value={"result": True})
        runner_mod.join_node(
            node="worker-1", role="worker",
            source="master-1", transport="minion",
        )
        upload_certs_calls = [c for c in runner_mod._cmd.call_args_list
                             if "upload-certs" in str(c)]
        assert len(upload_certs_calls) == 0


class TestNodeMeta:
    """Runner kubeadm.node_meta — labels и taints."""

    def test_applies_labels(self, runner_mod):
        """Labels из pillar применяются через kubectl."""
        runner_mod._cmd = MagicMock(return_value={"master-1": ""})
        runner_mod._get_node = MagicMock(return_value={
            "metadata": {"labels": {}, "annotations": {}}
        })
        runner_mod.node_meta(
            source="master-1", node="worker-1",
            labels={"topology.example.io/rack": "r1"},
            taints={}, transport="minion",
        )
        label_calls = [c for c in runner_mod._cmd.call_args_list
                       if "label" in str(c)]
        assert len(label_calls) >= 1

    def test_removes_managed_label(self, runner_mod):
        """Label удалённый из pillar, но в managed annotation → удаляется."""
        runner_mod._get_node = MagicMock(return_value={
            "metadata": {
                "labels": {"old-label": "val", "keep-label": "val"},
                "annotations": {
                    "kubeadm.salt/managed-labels": "old-label,keep-label"
                },
            }
        })
        runner_mod._cmd = MagicMock(return_value={"master-1": ""})
        runner_mod.node_meta(
            source="master-1", node="worker-1",
            labels={"keep-label": "val"},  # old-label убран
            taints={}, transport="minion",
        )
        remove_calls = [c for c in runner_mod._cmd.call_args_list
                        if "old-label-" in str(c)]  # kubectl label node X key-
        assert len(remove_calls) >= 1

    def test_foreign_labels_untouched(self, runner_mod):
        """Labels не в managed annotation не трогаются."""
        runner_mod._get_node = MagicMock(return_value={
            "metadata": {
                "labels": {"foreign": "val", "managed": "val"},
                "annotations": {"kubeadm.salt/managed-labels": "managed"},
            }
        })
        runner_mod._cmd = MagicMock(return_value={"master-1": ""})
        runner_mod.node_meta(
            source="master-1", node="worker-1",
            labels={},  # убрали managed
            taints={}, transport="minion",
        )
        all_cmds = str(runner_mod._cmd.call_args_list)
        assert "foreign" not in all_cmds

    def test_test_mode_shows_diff(self, runner_mod):
        """test=True → diff текущих vs желаемых labels."""
        runner_mod.__opts__["test"] = True
        runner_mod._get_node = MagicMock(return_value={
            "metadata": {"labels": {"existing": "old"}, "annotations": {}}
        })
        result = runner_mod.node_meta(
            source="master-1", node="worker-1",
            labels={"new-label": "val"}, taints={}, transport="minion",
        )
        assert result["result"] is None
        assert "new-label" in result.get("comment", "")


class TestPendingNodes:
    """Runner kubeadm.pending_nodes."""

    def test_excludes_joined_nodes(self, runner_mod):
        """Узлы уже в кластере исключаются."""
        runner_mod._nodegroup_members = MagicMock(
            return_value=["worker-1", "worker-2", "worker-3"]
        )
        runner_mod.nodes = MagicMock(return_value={
            "worker-1": {"status": "Ready"}
        })
        runner_mod.__opts__["pillar"] = {"kubeadm": {"orchestrate": {"decommissioned": []}}}
        result = runner_mod.pending_nodes(nodegroup="k8s-workers", transport="minion")
        assert "worker-1" not in result
        assert set(result) == {"worker-2", "worker-3"}

    def test_excludes_decommissioned(self, runner_mod):
        """Decommissioned узлы исключаются."""
        runner_mod._nodegroup_members = MagicMock(
            return_value=["worker-1", "worker-2"]
        )
        runner_mod.nodes = MagicMock(return_value={})
        runner_mod.__opts__["pillar"] = {
            "kubeadm": {"orchestrate": {"decommissioned": ["worker-2"]}}
        }
        result = runner_mod.pending_nodes(nodegroup="k8s-workers", transport="minion")
        assert result == ["worker-1"]
```

### Файл: `test/unit/test_state_joined.py`

```python
"""
Тесты для state kubeadm.joined.
"""
import pytest
from unittest.mock import MagicMock


class TestKubeadmJoined:
    """State kubeadm.joined."""

    def test_already_joined(self, state_mod):
        """kubelet.conf существует → no changes."""
        state_mod.__salt__["file.file_exists"] = MagicMock(return_value=True)
        result = state_mod.joined(name="join", config="/etc/kubernetes/kubeadm/join.yaml")
        assert result["result"] is True
        assert result["changes"] == {}

    def test_join_renders_config_0600(self, state_mod):
        """join.yaml рендерится с mode 0600."""
        state_mod.__salt__["file.file_exists"] = MagicMock(return_value=False)
        state_mod.__salt__["file.write"] = MagicMock()
        state_mod.__salt__["cmd.run_all"] = MagicMock(return_value={
            "retcode": 0, "stdout": "joined", "stderr": ""
        })
        state_mod.__salt__["file.remove"] = MagicMock()
        state_mod.joined(
            name="join",
            config="/etc/kubernetes/kubeadm/join.yaml",
            token="abcdef.0123456789abcdef",
            ca_hash="sha256:abc123",
        )
        write_call = state_mod.__salt__["file.write"].call_args
        # Проверяем что файл создаётся
        assert write_call is not None

    def test_join_config_deleted_after_use(self, state_mod):
        """join.yaml удаляется после join (содержит token)."""
        state_mod.__salt__["file.file_exists"] = MagicMock(return_value=False)
        state_mod.__salt__["file.write"] = MagicMock()
        state_mod.__salt__["cmd.run_all"] = MagicMock(return_value={
            "retcode": 0, "stdout": "joined", "stderr": ""
        })
        state_mod.__salt__["file.remove"] = MagicMock()
        state_mod.joined(
            name="join",
            config="/etc/kubernetes/kubeadm/join.yaml",
            token="token", ca_hash="hash",
        )
        state_mod.__salt__["file.remove"].assert_called()

    def test_test_mode_no_join(self, state_mod):
        """test=True → описание, join не выполняется."""
        state_mod.__opts__["test"] = True
        state_mod.__salt__["file.file_exists"] = MagicMock(return_value=False)
        result = state_mod.joined(
            name="join", config="/etc/kubernetes/kubeadm/join.yaml"
        )
        assert result["result"] is None
        state_mod.__salt__["cmd.run_all"].assert_not_called()

    def test_token_not_in_show_changes(self, state_mod):
        """Token не попадает в changes/comment (безопасность)."""
        state_mod.__salt__["file.file_exists"] = MagicMock(return_value=False)
        state_mod.__salt__["file.write"] = MagicMock()
        state_mod.__salt__["cmd.run_all"] = MagicMock(return_value={
            "retcode": 0, "stdout": "joined", "stderr": ""
        })
        state_mod.__salt__["file.remove"] = MagicMock()
        result = state_mod.joined(
            name="join", config="/etc/kubernetes/kubeadm/join.yaml",
            token="secret-token-value", ca_hash="hash",
        )
        result_str = str(result)
        assert "secret-token-value" not in result_str
```

### Файл: `test/unit/test_orch_join_worker.py`

```python
"""
Тесты для orch/kubeadm/join-worker.sls и join-worker-batch.sls.
"""
import pytest


class TestOrchJoinWorker:
    """Orchestrate join-worker."""

    def test_uses_pending_nodes_by_default(self, render_orch):
        """Без явных targets — pending_nodes."""
        states = render_orch("orch/kubeadm/join-worker.sls", orch_pillar())
        # Должен быть вызов pending_nodes
        runner_calls = [s for s in states if "pending_nodes" in str(s)]
        assert len(runner_calls) >= 1

    def test_explicit_targets(self, render_orch):
        """targets в pillar → используются напрямую."""
        pillar = orch_pillar()
        pillar["kubeadm"]["orchestrate"]["targets"] = ["worker-5"]
        states = render_orch("orch/kubeadm/join-worker.sls", pillar)
        # worker-5 должен быть в targets
        assert "worker-5" in str(states)

    def test_batching(self, render_orch):
        """Workers разбиваются на batches."""
        pillar = orch_pillar()
        pillar["kubeadm"]["orchestrate"]["batch_size"] = 2
        # Нужно более 2 pending workers для проверки batching
        # Проверяется integration тестом
        pass

    def test_event_per_batch(self, render_orch):
        """Event после каждого batch."""
        states = render_orch("orch/kubeadm/join-worker.sls", orch_pillar())
        events = [s for s in states if "event.send" in str(s)]
        assert any("join-worker" in str(e) for e in events)

    def test_ssh_transport(self, render_orch):
        """ssh transport → ssh: True на всех salt.state."""
        pillar = orch_pillar(transport="ssh")
        states = render_orch("orch/kubeadm/join-worker.sls", pillar)
        state_steps = [s for s in states if s.get("fun") == "salt.state"]
        for step in state_steps:
            assert step.get("kwarg", {}).get("ssh") is True
```

---

## TDD: порядок работы

### Шаг 1 — Тесты
1. `test/unit/test_runner_join.py`
2. `test/unit/test_state_joined.py`
3. `test/unit/test_orch_join_worker.py`

### Шаг 2 — Валидация тестов
- Token не в command line, не в diff, не в events
- join.yaml 0600, deleted after use
- Labels: managed annotation, foreign untouched
- Decommissioned excluded
- Batch + fail_fast

### Шаг 3 — Реализация
1. `_runners/kubeadm.py`: `join_node`, `nodes`, `pending_nodes`, `node_meta`
2. `_states/kubeadm.py`: `joined`
3. `orch/kubeadm/join-worker.sls`
4. `orch/kubeadm/join-worker-batch.sls`

### Шаг 4 — Тестирование
1. Unit: `pytest test/unit/ -v`
2. Integration: 2 workers joined, token не в `ps` и не в state output
3. Failure: failed worker reset, next batch stopped
4. Labels: удалённый label удалён с Node, foreign labels на месте

---

## Acceptance criteria

- [ ] 2 workers присоединены батчами; tokens не видны в state output и `ps`
- [ ] Induced failure: failed worker reset и удалён, следующий batch не стартовал
- [ ] Label убранный из pillar удалён с Node; foreign labels не тронуты
- [ ] Taints применены при join (register-with-taints)
- [ ] Decommissioned узлы не включаются в targets
- [ ] `test=True` показывает что было бы сделано, join не выполняется
- [ ] `replace: true` удаляет NotReady Node перед re-join
- [ ] `wait_ready: true` ждёт Ready (с CNI)
