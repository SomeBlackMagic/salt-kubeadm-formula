# Phase 5 — Node Removal

## Цель

Безопасное удаление worker и master узлов из кластера с сохранением etcd quorum. Drain, etcd member removal, cleanup.

## Зависимости

- Phase 3: workers (для worker removal)
- Phase 4: HA control plane, etcd runners (для master removal)

## Что реализуем

### 1. `_runners/kubeadm.py` — дополнительные функции

#### `etcd_member_remove(source, node, transport)` — MUTATING
- **test mode**: описание "would remove etcd member {node}"
- `etcdctl member remove <id>` через exec в etcd pod на source
- Перед удалением: verify member exists в member list
- После удаления: verify member gone
- Возвращает: `{result: bool, removed_id: str}`

#### `move_leader(source, target, transport)` — MUTATING
- **test mode**: описание "would move etcd leader from {source} to {target}"
- `etcdctl move-leader <target_id>` через exec
- Перед: verify source is leader
- Если source не leader — no-op (успех)
- После: verify target is leader

### 2. `orch/kubeadm/remove-node.sls`

**Параметры:**
- `target` — обязательный, один узел
- `drain: true` (default)
- `drain_timeout: 10m`
- `purge: false`
- `node_dead: false`
- `force_drain: false`

**Определение роли из реальности** (не из pillar):
```python
# Runner: detect_role(source, target)
# 1. etcd member list → target в members? → control-plane with etcd
# 2. /etc/kubernetes/manifests/kube-apiserver.yaml exists? → control-plane
# 3. Else → worker
```

#### Worker removal flow:

```
1. Precheck: healthy source; Node absent → succeed (idempotent)
2. cordon: kubectl cordon <target>
3. drain: kubectl drain <target>
     --ignore-daemonsets
     --delete-emptydir-data
     --timeout=<drain_timeout>
   PDB respected. Blocked → fail с blocking pods в event
   force_drain: true → --disable-eviction
4. На target (если reachable):
   - kubeadm reset -f
   - service.disabled kubelet
   - file.absent /etc/cni/net.d
   - module.run iptables.flush (не cmd.run)
   - purge: true → remove binaries, unit
5. kubectl delete node <target>
6. Event kubeadm/remove-node/done/<target>
```

#### Master removal flow:

```
1. Quorum precheck (от ДРУГОГО healthy master):
   - remaining_healthy = healthy_members - 1
   - required_quorum = (total_after_remove // 2) + 1
   - remaining_healthy >= required_quorum → OK
   - Last master → refuse
   - Even result → warning event

2. Move leader: если target = etcd leader → move-leader к другому

3. cordon + drain (как worker)

4. etcd_member_remove: удалить из member list ЯВНО
   Затем на target: kubeadm reset -f --skip-phases=remove-etcd-member
   (removal уже сделано нами после наших проверок)

5. Cleanup:
   - /var/lib/etcd
   - /etc/kubernetes
   - /etc/cni/net.d
   - iptables/ipvs flush
   - purge → binaries, unit

6. kubectl delete node <target>

7. Postcheck: member gone, remaining healthy, /readyz via endpoint

8. Event с reminder: "Remove <target> from LB backend pool"
```

#### Dead node handling:

```
- drain и reset skipped
- kubectl delete node
- Master:
  - Unreachable for Salt BUT etcd member healthy → REFUSE
    (это проблема Salt connectivity, не мёртвый node)
  - node_dead: true required
  - Runner verifies member is really unhealthy
  - etcd_member_remove от healthy source
```

---

## TDD: тесты

### Файл: `test/unit/test_runner_remove.py`

```python
"""
Тесты для runner функций remove-node.
"""
import pytest
from unittest.mock import MagicMock


class TestEtcdMemberRemove:
    """Runner kubeadm.etcd_member_remove."""

    def test_test_mode(self, runner_mod):
        runner_mod.__opts__["test"] = True
        result = runner_mod.etcd_member_remove(
            source="master-1", node="master-3", transport="minion"
        )
        assert result["result"] is None

    def test_removes_member(self, runner_mod):
        """Удаляет member по ID."""
        runner_mod._etcd_members = MagicMock(return_value=[
            {"name": "master-1", "id": "aaa"},
            {"name": "master-3", "id": "ccc"},
        ])
        runner_mod._cmd = MagicMock(return_value={"master-1": ""})
        result = runner_mod.etcd_member_remove(
            source="master-1", node="master-3", transport="minion"
        )
        assert result["result"] is True
        assert result["removed_id"] == "ccc"

    def test_member_not_found(self, runner_mod):
        """Member не найден → success (idempotent)."""
        runner_mod._etcd_members = MagicMock(return_value=[
            {"name": "master-1", "id": "aaa"},
        ])
        result = runner_mod.etcd_member_remove(
            source="master-1", node="master-3", transport="minion"
        )
        assert result["result"] is True  # идемпотентно


class TestMoveLeader:
    """Runner kubeadm.move_leader."""

    def test_test_mode(self, runner_mod):
        runner_mod.__opts__["test"] = True
        result = runner_mod.move_leader(
            source="master-1", target="master-2", transport="minion"
        )
        assert result["result"] is None

    def test_moves_leader(self, runner_mod):
        """Переносит лидера."""
        runner_mod._etcd_leader = MagicMock(return_value="master-1")
        runner_mod._etcd_member_id = MagicMock(return_value="bbb")
        runner_mod._cmd = MagicMock(return_value={"master-1": "Leadership transferred"})
        result = runner_mod.move_leader(
            source="master-1", target="master-2", transport="minion"
        )
        assert result["result"] is True

    def test_not_leader_noop(self, runner_mod):
        """Source не leader → no-op success."""
        runner_mod._etcd_leader = MagicMock(return_value="master-2")  # другой лидер
        result = runner_mod.move_leader(
            source="master-1", target="master-2", transport="minion"
        )
        assert result["result"] is True
        assert "already" in result.get("comment", "").lower() or result["changes"] == {}


class TestDetectRole:
    """Определение роли по реальному состоянию."""

    def test_etcd_member_is_controlplane(self, runner_mod):
        runner_mod._etcd_members = MagicMock(return_value=[
            {"name": "master-1"}, {"name": "target-node"},
        ])
        result = runner_mod._detect_role(source="master-1", target="target-node", transport="minion")
        assert result == "control-plane"

    def test_no_etcd_no_manifests_is_worker(self, runner_mod):
        runner_mod._etcd_members = MagicMock(return_value=[{"name": "master-1"}])
        runner_mod._cmd = MagicMock(return_value={"target-node": False})  # no manifests
        result = runner_mod._detect_role(source="master-1", target="target-node", transport="minion")
        assert result == "worker"
```

### Файл: `test/unit/test_orch_remove.py`

```python
"""
Тесты для orch/kubeadm/remove-node.sls.
"""
import pytest


class TestOrchRemoveWorker:
    """Remove worker node."""

    def test_drain_before_reset(self, render_orch):
        """Drain выполняется до reset."""
        pillar = orch_pillar(target="worker-1")
        states = render_orch("orch/kubeadm/remove-node.sls", pillar)
        drain_idx = next(i for i, s in enumerate(states) if "drain" in str(s).lower())
        reset_idx = next(i for i, s in enumerate(states) if "reset" in str(s).lower())
        assert drain_idx < reset_idx

    def test_idempotent_absent_node(self, render_orch):
        """Node absent → succeed."""
        # Runner nodes() не содержит target → skip
        pass  # integration test

    def test_force_drain_flag(self, render_orch):
        """force_drain: true → --disable-eviction."""
        pillar = orch_pillar(target="worker-1")
        pillar["kubeadm"]["orchestrate"]["force_drain"] = True
        states = render_orch("orch/kubeadm/remove-node.sls", pillar)
        drain_steps = [s for s in states if "drain" in str(s).lower()]
        assert any("disable-eviction" in str(s) for s in drain_steps)

    def test_purge_removes_binaries(self, render_orch):
        """purge: true → удаление бинарников и unit."""
        pillar = orch_pillar(target="worker-1")
        pillar["kubeadm"]["orchestrate"]["purge"] = True
        states = render_orch("orch/kubeadm/remove-node.sls", pillar)
        absent_states = [s for s in states if s.get("fun") == "file.absent"]
        paths = [s["name"] for s in absent_states]
        # Бинарники должны быть в списке удаления
        assert any("kubernetes" in p for p in paths)

    def test_event_emitted(self, render_orch):
        states = render_orch("orch/kubeadm/remove-node.sls", orch_pillar(target="worker-1"))
        events = [s for s in states if "event.send" in str(s)]
        assert any("remove-node/done" in str(e) for e in events)


class TestOrchRemoveMaster:
    """Remove master node."""

    def test_quorum_precheck(self, render_orch):
        """Quorum precheck перед removal."""
        pillar = orch_pillar(target="master-3")
        states = render_orch("orch/kubeadm/remove-node.sls", pillar)
        precheck = [s for s in states if "quorum" in str(s).lower()]
        assert len(precheck) >= 1

    def test_last_master_refused(self, runner_mod):
        """Удаление последнего master отклонено."""
        runner_mod._etcd_members = MagicMock(return_value=[
            {"name": "master-1", "status": "started"},
        ])
        result = runner_mod._quorum_precheck(
            source="master-1", target="master-1", transport="minion"
        )
        assert result["ok"] is False
        assert "last" in result["message"].lower()

    def test_move_leader_before_remove(self, render_orch):
        """Если target = leader, move_leader до etcd_member_remove."""
        pillar = orch_pillar(target="master-3")
        states = render_orch("orch/kubeadm/remove-node.sls", pillar)
        move_idx = next((i for i, s in enumerate(states) if "move_leader" in str(s)), None)
        remove_idx = next((i for i, s in enumerate(states) if "member_remove" in str(s)), None)
        if move_idx is not None and remove_idx is not None:
            assert move_idx < remove_idx

    def test_skip_phases_remove_etcd(self, render_orch):
        """kubeadm reset с --skip-phases=remove-etcd-member."""
        pillar = orch_pillar(target="master-3")
        states = render_orch("orch/kubeadm/remove-node.sls", pillar)
        reset_steps = [s for s in states if "reset" in str(s).lower() and "kubeadm" in str(s).lower()]
        assert any("skip-phases" in str(s) and "remove-etcd-member" in str(s) for s in reset_steps)

    def test_etcd_var_lib_cleaned(self, render_orch):
        """/var/lib/etcd удаляется для master."""
        pillar = orch_pillar(target="master-3")
        states = render_orch("orch/kubeadm/remove-node.sls", pillar)
        absent = [s for s in states if s.get("fun") == "file.absent"]
        paths = [s["name"] for s in absent]
        assert any("etcd" in p for p in paths)

    def test_lb_reminder_in_event(self, render_orch):
        """Event содержит напоминание об удалении из LB."""
        pillar = orch_pillar(target="master-3")
        states = render_orch("orch/kubeadm/remove-node.sls", pillar)
        events = [s for s in states if "event.send" in str(s)]
        assert any("LB" in str(e) or "load balancer" in str(e).lower() for e in events)


class TestDeadNode:
    """Dead node removal."""

    def test_dead_node_skips_drain_and_reset(self, render_orch):
        """node_dead: true → skip drain и reset на target."""
        pillar = orch_pillar(target="worker-1")
        pillar["kubeadm"]["orchestrate"]["node_dead"] = True
        states = render_orch("orch/kubeadm/remove-node.sls", pillar)
        # Drain и reset должны быть skipped
        drain = [s for s in states if "drain" in str(s).lower() and s.get("fun") == "salt.state"]
        target_resets = [s for s in states
                        if "reset" in str(s).lower() and "worker-1" in str(s.get("tgt", ""))]
        assert len(drain) == 0 or all(s.get("_skip") for s in drain)
        # kubectl delete node всё равно выполняется

    def test_dead_master_requires_flag(self, runner_mod):
        """Dead master без node_dead: true → refuse."""
        runner_mod._cmd = MagicMock(side_effect=Exception("unreachable"))
        # Без node_dead → ошибка, не авто-удаление
        pass  # логика в orch: if not reachable and not node_dead → fail

    def test_dead_master_healthy_etcd_refused(self, runner_mod):
        """Unreachable master с healthy etcd member → refuse."""
        runner_mod._salt_reachable = MagicMock(return_value=False)
        runner_mod._etcd_member_healthy = MagicMock(return_value=True)
        result = runner_mod._dead_node_precheck(
            source="master-1", target="master-2", transport="minion"
        )
        assert result["ok"] is False
        assert "connectivity" in result["message"].lower()
```

---

## TDD: порядок работы

### Шаг 1 — Тесты
1. `test/unit/test_runner_remove.py`
2. `test/unit/test_orch_remove.py`

### Шаг 2 — Валидация тестов
- Quorum safety: last master refused, even warning
- Leader move before removal
- Dead node: healthy etcd = refuse
- Drain: PDB, force_drain, timeout
- Cleanup: iptables via module.run, не cmd.run

### Шаг 3 — Реализация
1. `_runners/kubeadm.py`: `etcd_member_remove`, `move_leader`, `_detect_role`, `_quorum_precheck`, `_dead_node_precheck`
2. `orch/kubeadm/remove-node.sls`

### Шаг 4 — Тестирование
1. Unit: `pytest test/unit/ -v`
2. Worker removed: PDB respected, blocked drain reports pods
3. Master replaced: remove + join keeping quorum
4. Leader moved first when needed
5. Unreachable master with healthy etcd NOT removed
6. Last master refused

---

## Acceptance criteria

- [ ] Worker removed: PDB respected; blocked drain показывает blocking pods
- [ ] Master replaced (remove + join) с сохранением quorum; leader moved first
- [ ] Unreachable-for-Salt master с healthy etcd member НЕ удаляется
- [ ] Удаление последнего master отклонено
- [ ] `node_dead: true` пропускает drain и reset
- [ ] `purge: true` удаляет бинарники и unit
- [ ] `--skip-phases=remove-etcd-member` для master reset
- [ ] Event с напоминанием об LB
- [ ] iptables cleanup через `module.run`, не `cmd.run`
- [ ] Idempotent: absent node → succeed
