# Phase 2 — Bootstrap (декомпозиция)

Создание первого master-узла кластера. Разбита на атомарные подфазы.

## Граф зависимостей

```
2.4 (transport) ──┬──→ 2.5 (cluster_exists) ──→ 2.9 (orch)
                  └──→ 2.6 (pick_*)          ──→ 2.9
2.1 (initialized) ──→ 2.7 (init_cluster.sls) ──→ 2.9
2.2 (healthy)     ──→ 2.7
2.3 (preflight)   ──→ 2.7
2.8 (reset.sls)        # независимый
```

## Подфазы

| # | Файл | Что | Статус |
|---|------|-----|--------|
| 2.1 | [2.1-state-initialized.md](2.1-state-initialized.md) | State `kubeadm.initialized` | done |
| 2.2 | [2.2-state-healthy.md](2.2-state-healthy.md) | State `kubeadm.healthy` | done |
| 2.3 | [2.3-state-preflight.md](2.3-state-preflight.md) | State `kubeadm.preflight` | done |
| 2.4 | [2.4-runner-transport.md](2.4-runner-transport.md) | Runner transport layer | done |
| 2.5 | [2.5-runner-cluster-exists.md](2.5-runner-cluster-exists.md) | Runner `cluster_exists` | done |
| 2.6 | [2.6-runner-pick-node.md](2.6-runner-pick-node.md) | Runners `pick_init_node`, `pick_source` | done |
| 2.7 | [2.7-sls-init-cluster.md](2.7-sls-init-cluster.md) | SLS `kubeadm/init_cluster.sls` | todo |
| 2.8 | [2.8-sls-reset.md](2.8-sls-reset.md) | SLS `kubeadm/reset.sls` | todo |
| 2.9 | [2.9-orch-bootstrap.md](2.9-orch-bootstrap.md) | Orchestrate bootstrap | todo |

## Параллельность

Независимо друг от друга: 2.1, 2.2, 2.3, 2.4, 2.8.
Финальная сборка (2.9) требует все остальные.
