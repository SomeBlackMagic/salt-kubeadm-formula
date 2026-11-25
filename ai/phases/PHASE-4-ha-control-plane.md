# Phase 4 — HA Control Plane

## Цель

Присоединение дополнительных master-узлов (control-plane) с безопасным управлением etcd quorum. Кластер становится HA: 3+ masters.

## Зависимости

- Phase 2: bootstrap (single master)
- Phase 3: `join_node`, `joined` state

## Что реализуем

### 1. `_runners/kubeadm.py` — etcd и CP функции

#### `cp_members(source, transport)` — read-only
- `etcdctl member list` через exec в etcd pod на source
- `kubectl get nodes` с label `node-role.kubernetes.io/control-plane`
- Возвращает: `{members: [{name, peerURL, status}], nodes: [node_names]}`

#### `etcd_precheck(source, joining, transport)` — read-only
- Все существующие members started и healthy
- Нет member с тем же name или peer URL как у joining node
- Любой unhealthy member → refuse с описанием проблемы
- Возвращает: `{ok: bool, message: str, members: [...]}`

#### `etcd_postcheck(source, joined, transport)` — read-only
- Новый member started и присутствует в member list
- Все members healthy (endpoint health)
- Возвращает: `{ok: bool, message: str}`

#### `join_rollback(source, node, transport)` — MUTATING
- **test mode**: описание "would rollback {node}"
- Удаляет unstarted etcd member из member list (если присутствует)
- Запускает `kubeadm reset -f` на target node
- Возвращает кластер в состояние до join

### 2. `orch/kubeadm/join-controlplane.sls`

Outer orchestrate: выбор targets, последовательный вызов per-node.

```
1. Source: pick_source
2. Targets: pillar targets ИЛИ (masters_nodegroup members - cp_members - decommissioned)
3. Even count warning: if (current_cp + len(targets)) % 2 == 0 → event warning
4. Для каждого target последовательно:
   a. salt.runner state.orchestrate orch.kubeadm.join-controlplane-node
      с pillar: {target: node, source: source}
      и test: {{ opts['test'] }}
   b. require от предыдущего шага (останов при ошибке)
5. Cleanup: kubectl delete secret kubeadm-certs -n kube-system (после последнего master)
```

### 3. `orch/kubeadm/join-controlplane-node.sls`

Per-master nested orchestrate:

```
1. etcd_precheck: все members healthy, нет дублей
   → fail если unhealthy

2. Containerd + node states на target

3. Preflight: kubeadm join phase preflight

4. Join через join_node(role="control-plane"):
   - Fresh token (TTL 15m)
   - CA hash
   - Certificate key (upload-certs, живёт 2h)
   - join.yaml (0600, no diff, deleted after)
   - kubeadm join --config --patches
   - Guard: unless kubelet.conf exists

5. etcd_postcheck: new member started, all healthy

6. Health checks:
   - Local /readyz OK
   - Static pods running (mirror pod hash)
   - kubelet active
   - Node exists in kubectl get nodes

7. Event: kubeadm/join-controlplane/done/<node>

ROLLBACK (onfail join или postcheck):
   - join_rollback: remove unstarted etcd member + kubeadm reset on target
   - Scenario stops (не продолжает к следующему master)
```

### 4. Quorum safety logic

```
Текущее состояние → после join → quorum
1 member → 2 members → quorum = 2 (RISK: потеря 1 = потеря кластера)
2 members → 3 members → quorum = 2 (безопасно)
3 members → 4 members → quorum = 3 (even warning)
```

- 1 → 2: emit warning "quorum becomes 2, recommend joining to 3 back-to-back"
- Even count: emit warning "even number of masters, recommend odd"
- Removing joined-but-unhealthy member: only via remove-node (Phase 5)

---

## TDD: тесты

### Файл: `test/unit/test_runner_etcd.py`

```python
"""
Тесты для etcd-related runner функций.
"""
import pytest
from unittest.mock import MagicMock


class TestCpMembers:
    """Runner kubeadm.cp_members."""

    def test_returns_members_and_nodes(self, runner_mod):
        """Возвращает etcd members и CP nodes."""
        runner_mod._cmd = MagicMock(side_effect=[
            # etcdctl member list
            {"master-1": "id1, started, master-1, https://10.0.0.11:2380, https://10.0.0.11:2379, false\n"
                         "id2, started, master-2, https://10.0.0.12:2380, https://10.0.0.12:2379, false"},
            # kubectl get nodes
            {"master-1": '{"items": [{"metadata": {"name": "master-1"}}, {"metadata": {"name": "master-2"}}]}'},
        ])
        result = runner_mod.cp_members(source="master-1", transport="minion")
        assert len(result["members"]) == 2
        assert "master-1" in [m["name"] for m in result["members"]]
        assert "master-2" in result["nodes"]


class TestEtcdPrecheck:
    """Runner kubeadm.etcd_precheck."""

    def test_all_healthy(self, runner_mod):
        """Все members healthy → ok."""
        runner_mod._etcd_health = MagicMock(return_value={
            "master-1": {"healthy": True},
            "master-2": {"healthy": True},
        })
        runner_mod._etcd_members = MagicMock(return_value=[
            {"name": "master-1", "peerURL": "https://10.0.0.11:2380", "status": "started"},
            {"name": "master-2", "peerURL": "https://10.0.0.12:2380", "status": "started"},
        ])
        result = runner_mod.etcd_precheck(
            source="master-1", joining="master-3", transport="minion"
        )
        assert result["ok"] is True

    def test_unhealthy_member_refuses(self, runner_mod):
        """Unhealthy member → refuse."""
        runner_mod._etcd_health = MagicMock(return_value={
            "master-1": {"healthy": True},
            "master-2": {"healthy": False, "error": "connection refused"},
        })
        runner_mod._etcd_members = MagicMock(return_value=[
            {"name": "master-1", "status": "started"},
            {"name": "master-2", "status": "started"},
        ])
        result = runner_mod.etcd_precheck(
            source="master-1", joining="master-3", transport="minion"
        )
        assert result["ok"] is False
        assert "master-2" in result["message"]

    def test_duplicate_name_refuses(self, runner_mod):
        """Member с тем же name → refuse."""
        runner_mod._etcd_health = MagicMock(return_value={
            "master-1": {"healthy": True},
        })
        runner_mod._etcd_members = MagicMock(return_value=[
            {"name": "master-1", "status": "started"},
        ])
        result = runner_mod.etcd_precheck(
            source="master-1", joining="master-1", transport="minion"
        )
        assert result["ok"] is False
        assert "already" in result["message"].lower()

    def test_duplicate_peer_url_refuses(self, runner_mod):
        """Member с тем же peer URL → refuse."""
        runner_mod._etcd_health = MagicMock(return_value={
            "master-1": {"healthy": True},
        })
        runner_mod._etcd_members = MagicMock(return_value=[
            {"name": "master-1", "peerURL": "https://10.0.0.11:2380", "status": "started"},
        ])
        result = runner_mod.etcd_precheck(
            source="master-1", joining="master-new",
            joining_peer_url="https://10.0.0.11:2380",  # дубль!
            transport="minion"
        )
        assert result["ok"] is False


class TestEtcdPostcheck:
    """Runner kubeadm.etcd_postcheck."""

    def test_new_member_healthy(self, runner_mod):
        """Новый member started и все healthy → ok."""
        runner_mod._etcd_health = MagicMock(return_value={
            "master-1": {"healthy": True},
            "master-3": {"healthy": True},
        })
        runner_mod._etcd_members = MagicMock(return_value=[
            {"name": "master-1", "status": "started"},
            {"name": "master-3", "status": "started"},
        ])
        result = runner_mod.etcd_postcheck(
            source="master-1", joined="master-3", transport="minion"
        )
        assert result["ok"] is True

    def test_new_member_not_started(self, runner_mod):
        """Новый member в member list но не started → fail."""
        runner_mod._etcd_members = MagicMock(return_value=[
            {"name": "master-1", "status": "started"},
            {"name": "master-3", "status": "unstarted"},
        ])
        result = runner_mod.etcd_postcheck(
            source="master-1", joined="master-3", transport="minion"
        )
        assert result["ok"] is False


class TestJoinRollback:
    """Runner kubeadm.join_rollback."""

    def test_test_mode(self, runner_mod):
        """test=True → описание, без действий."""
        runner_mod.__opts__["test"] = True
        result = runner_mod.join_rollback(
            source="master-1", node="master-3", transport="minion"
        )
        assert result["result"] is None

    def test_removes_unstarted_member(self, runner_mod):
        """Удаляет unstarted member из etcd."""
        runner_mod._etcd_members = MagicMock(return_value=[
            {"name": "master-1", "id": "id1", "status": "started"},
            {"name": "master-3", "id": "id3", "status": "unstarted"},
        ])
        runner_mod._cmd = MagicMock(return_value={"master-1": ""})
        runner_mod.join_rollback(
            source="master-1", node="master-3", transport="minion"
        )
        # etcdctl member remove вызван
        remove_calls = [c for c in runner_mod._cmd.call_args_list
                        if "member remove" in str(c)]
        assert len(remove_calls) >= 1

    def test_resets_target_node(self, runner_mod):
        """kubeadm reset выполняется на target."""
        runner_mod._etcd_members = MagicMock(return_value=[])
        runner_mod._cmd = MagicMock(return_value={"master-3": ""})
        runner_mod._apply_state = MagicMock(return_value={"result": True})
        runner_mod.join_rollback(
            source="master-1", node="master-3", transport="minion"
        )
        # reset вызван на target
        reset_calls = [c for c in runner_mod._apply_state.call_args_list
                       if "reset" in str(c)]
        assert len(reset_calls) >= 1

    def test_no_member_still_resets(self, runner_mod):
        """Если member не найден — всё равно reset на target."""
        runner_mod._etcd_members = MagicMock(return_value=[
            {"name": "master-1", "id": "id1", "status": "started"},
            # master-3 отсутствует
        ])
        runner_mod._cmd = MagicMock(return_value={})
        runner_mod._apply_state = MagicMock(return_value={"result": True})
        runner_mod.join_rollback(
            source="master-1", node="master-3", transport="minion"
        )
        # reset всё равно вызван
        assert runner_mod._apply_state.called
```

### Файл: `test/unit/test_orch_join_cp.py`

```python
"""
Тесты для orch/kubeadm/join-controlplane.sls.
"""
import pytest


class TestOrchJoinControlplane:
    """Orchestrate join-controlplane."""

    def test_sequential_per_master(self, render_orch):
        """Masters присоединяются один за одним (require chain)."""
        pillar = orch_pillar()
        states = render_orch("orch/kubeadm/join-controlplane.sls", pillar)
        # Каждый nested orch зависит от предыдущего
        nested = [s for s in states
                  if "join-controlplane-node" in str(s.get("sls", ""))]
        for i in range(1, len(nested)):
            assert nested[i].get("require") is not None

    def test_etcd_precheck_before_join(self, render_orch):
        """etcd precheck выполняется до join."""
        states = render_orch(
            "orch/kubeadm/join-controlplane-node.sls",
            orch_pillar(target="master-2", source="master-1"),
        )
        precheck_idx = next(i for i, s in enumerate(states) if "etcd_precheck" in str(s))
        join_idx = next(i for i, s in enumerate(states) if "join" in str(s.get("sls", "")))
        assert precheck_idx < join_idx

    def test_rollback_on_join_failure(self, render_orch):
        """onfail join → join_rollback."""
        states = render_orch(
            "orch/kubeadm/join-controlplane-node.sls",
            orch_pillar(target="master-2", source="master-1"),
        )
        rollback = [s for s in states if "join_rollback" in str(s)]
        assert len(rollback) >= 1
        # rollback должен быть onfail от join шага
        for r in rollback:
            assert "onfail" in r or "onfail_any" in r

    def test_cleanup_kubeadm_certs_secret(self, render_orch):
        """После последнего master — удаление Secret kubeadm-certs."""
        states = render_orch("orch/kubeadm/join-controlplane.sls", orch_pillar())
        cleanup = [s for s in states if "kubeadm-certs" in str(s)]
        assert len(cleanup) >= 1

    def test_even_count_warning(self, render_orch):
        """Чётное число masters → warning event."""
        # 1 existing + 1 joining = 2 (even)
        pillar = orch_pillar()
        pillar["kubeadm"]["orchestrate"]["_current_cp_count"] = 1
        pillar["kubeadm"]["orchestrate"]["targets"] = ["master-2"]
        states = render_orch("orch/kubeadm/join-controlplane.sls", pillar)
        warnings = [s for s in states if "even" in str(s).lower() or "warning" in str(s).lower()]
        assert len(warnings) >= 1

    def test_cert_key_for_cp_join(self, render_orch):
        """Control-plane join получает certificate key."""
        states = render_orch(
            "orch/kubeadm/join-controlplane-node.sls",
            orch_pillar(target="master-2", source="master-1"),
        )
        # join_node должен вызываться с role="control-plane"
        join_calls = [s for s in states if "join_node" in str(s)]
        assert any("control-plane" in str(j) for j in join_calls)
```

---

## TDD: порядок работы

### Шаг 1 — Тесты
1. `test/unit/test_runner_etcd.py`
2. `test/unit/test_orch_join_cp.py`

### Шаг 2 — Валидация тестов
- etcd precheck: healthy, duplicate name/URL
- Postcheck: new member started
- Rollback: remove unstarted member + reset
- Sequential execution with require chain
- Even count warning

### Шаг 3 — Реализация
1. `_runners/kubeadm.py`: `cp_members`, `etcd_precheck`, `etcd_postcheck`, `join_rollback`
2. `orch/kubeadm/join-controlplane.sls`
3. `orch/kubeadm/join-controlplane-node.sls`

### Шаг 4 — Тестирование
1. Unit: `pytest test/unit/ -v`
2. Integration: 1 → 3 masters, etcd healthy after each
3. Join refused with unhealthy etcd member
4. Induced failure: rollback restores member list
5. Join works with one master stopped (auto source selection)

---

## Acceptance criteria

- [ ] 1 → 3 masters, один за одним, etcd healthy после каждого
- [ ] Join отклонён при unhealthy etcd member
- [ ] Induced failure после member add: rollback оставляет `member list` как до join
- [ ] Join работает с одним остановленным master (auto source selection)
- [ ] Certificate key генерируется для CP join, не для workers
- [ ] Secret `kubeadm-certs` удаляется после последнего master
- [ ] Even count → warning event
- [ ] `test=True` — ничего не меняет, показывает precheck результат
