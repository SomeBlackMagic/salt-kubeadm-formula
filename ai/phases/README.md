# salt-kubeadm-formula — Phased Implementation Plan (TDD)

## Общий TDD-цикл для каждой фазы

```
1. ТЕСТЫ       → Пишем unit/integration тесты для всех функций фазы
2. ВАЛИДАЦИЯ    → Проверяем что тесты корректны, покрывают edge cases, все FAIL
3. РЕАЛИЗАЦИЯ   → Пишем код до прохождения всех тестов
4. ТЕСТИРОВАНИЕ → Прогоняем тесты фазы + полный прогон всех предыдущих фаз
```

## Принципы

- **Избегаем `cmd.run`** где возможно:
  - Файлы: `file.managed`, `file.symlink`, `file.absent`, `file.directory`
  - HTTP: `http.query` вместо `curl`
  - Сервисы: `service.running`, `service.enabled`, `service.status`
  - iptables: `module.run iptables.flush`
  - daemon-reload: `module.run service.systemctl_reload`
  - `cmd.run_all` только внутри custom states для вызова kubeadm/kubectl/etcdctl — внешних бинарников
- **Custom states** (`_states/kubeadm.py`) оборачивают все kubeadm-вызовы с real `test=True` diffs
- **Custom runners** (`_runners/kubeadm.py`) — cluster-level ops, read-only vs mutating separation
- **Pure helpers** (`_modules/kubeadm_util.py`) — без side effects, полностью unit-testable

## Фазы

| Phase | Название | Что делает | Ключевые файлы |
|-------|----------|------------|-----------------|
| [0](PHASE-0-foundations.md) | Foundations | merge_layers, to_k8s, denylist, defaults | `_modules/kubeadm_util.py`, `defaults.yaml`, `map.jinja` |
| [1](PHASE-1-node-install.md) | Node Install | binaries, kubelet unit, config, patches | `binaries.sls`, `kubelet.sls`, `config.sls` |
| [2](PHASE-2-bootstrap.md) | Bootstrap | First master, kubeadm init, health | `_states/kubeadm.py`, `_runners/kubeadm.py`, `orch/bootstrap.sls` |
| [3](PHASE-3-workers.md) | Workers | Worker join, labels, taints, batches | `join-worker.sls`, `join-worker-batch.sls` |
| [4](PHASE-4-ha-control-plane.md) | HA Control Plane | CP join, etcd quorum, rollback | `join-controlplane.sls`, `join-controlplane-node.sls` |
| [5](PHASE-5-remove-node.md) | Remove Node | Drain, etcd remove, dead node | `remove-node.sls` |
| [6](PHASE-6-reconfigure.md) | Reconfigure | Kubelet/CP config, backup, rollback | `reconfigure.sls`, `reconfigure-kubelet.sls`, `reconfigure-controlplane.sls` |
| [7](PHASE-7-certificates.md) | Certificates | Renew script, timer, orchestrate | `certs/init.sls`, `certs/check.sls`, `orch/certs.sls` |
| [8](PHASE-8-addons.md) | Addons | kube-proxy, CoreDNS, SSA | `addons.sls`, `orch/addons.sls` |
| [9](PHASE-9-upgrade.md) | Upgrade | Version upgrade, snapshot, symlinks | `orch/upgrade.sls`, `upgrade-node.sls` |

## Зависимости между фазами

```
Phase 0 ──→ Phase 1 ──→ Phase 2 ──→ Phase 3 ──→ Phase 5
                              │            │
                              ├──→ Phase 4 ─┘
                              │
                              ├──→ Phase 6
                              │        │
                              │        ├──→ Phase 7
                              │        │
                              ├──→ Phase 8
                              │
                              └──→ Phase 9 (depends on 1, 4, 6, 7, 8)
```

## Тестовая структура

```
test/
  unit/
    conftest.py                  # фикстуры: state_mod, runner_mod, render_sls, render_orch
    test_kubeadm_util.py         # Phase 0: pure helpers
    test_map_jinja.py            # Phase 0: pillar merge + denylist
    test_binaries_render.py      # Phase 1: binaries SLS
    test_kubelet_render.py       # Phase 1: kubelet SLS
    test_config_render.py        # Phase 1: config SLS
    test_flags_rendering.py      # Phase 1: KUBELET_EXTRA_ARGS
    test_state_kubeadm.py        # Phase 2: initialized, healthy
    test_runner_kubeadm.py       # Phase 2: cluster_exists, pick_source
    test_orch_bootstrap.py       # Phase 2: orch bootstrap
    test_runner_join.py          # Phase 3: join_node, node_meta
    test_state_joined.py         # Phase 3: joined state
    test_orch_join_worker.py     # Phase 3: orch join-worker
    test_runner_etcd.py          # Phase 4: etcd precheck/postcheck, rollback
    test_orch_join_cp.py         # Phase 4: orch join-controlplane
    test_runner_remove.py        # Phase 5: etcd_member_remove, move_leader
    test_orch_remove.py          # Phase 5: orch remove-node
    test_state_reconfigure.py    # Phase 6: kubelet_config_applied, control_plane_applied
    test_runner_diff.py          # Phase 6: diff, upload_config
    test_orch_reconfigure.py     # Phase 6: orch reconfigure
    test_certs_renew_script.py   # Phase 7: renew logic
    test_certs_sls.py            # Phase 7: certs SLS
    test_orch_certs.py           # Phase 7: orch certs
    test_state_addon.py          # Phase 8: addon_applied
    test_orch_addons.py          # Phase 8: orch addons
    test_runner_upgrade.py       # Phase 9: versions, etcd_snapshot, prechecks
    test_state_upgraded.py       # Phase 9: upgraded state
    test_orch_upgrade.py         # Phase 9: orch upgrade
  integration/
    # VM-based tests per phase (salt-call, salt-run)
```

## cmd.run audit — где допустим

| Место | Что вызывает | Почему нельзя избежать |
|-------|-------------|----------------------|
| `_states/kubeadm.py` | `kubeadm init/join/upgrade/reset`, `kubeadm certs` | Внешний бинарник |
| `_runners/kubeadm.py` | `kubectl`, `etcdctl` (через exec), `kubeadm token` | Внешние бинарники, cluster API |
| `certs/check.sls` | Renew script | Внешний скрипт |
| `kubeadm/reset.sls` | `kubeadm reset` | Внешний бинарник |

Везде остальное — Salt states и modules.
