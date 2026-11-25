# Phase 0 — Foundations

## Цель

Базовая инфраструктура формулы: система слияния pillar-слоёв, преобразование named lists, валидация denylists, рендеринг конфигов. Без этой фазы ничего не работает.

## Что реализуем

### 1. `_modules/kubeadm_util.py` — чистые функции без side effects

#### `merge_layers(defaults, cluster, role, node)`
- Рекурсивное слияние словарей: `defaults.yaml` → `cluster` → `roles[role]` → `node`
- Вложенные dict мержатся рекурсивно, списки заменяются целиком
- `None` значения в верхнем слое удаляют ключ из результата (явное удаление)
- Возвращает финальный merged dict

#### `to_k8s(obj)`
- Рекурсивно обходит dict
- Ключи из набора named-list keys (`containers`, `initContainers`, `env`, `volumes`, `volumeMounts`, `ports`) преобразует из dict-of-dicts в list-of-dicts с полем `name`
- Пример: `{containers: {nginx: {image: "nginx:1.25"}}}` → `{containers: [{name: nginx, image: "nginx:1.25"}]}`
- Остальные ключи остаются как есть
- Набор named-list keys читается из `defaults.yaml`

#### `extra_args(args, api_version)`
- `v1beta3`: возвращает dict as-is (kubeadm 1.25–1.30 принимает map)
- `v1beta4`: преобразует dict в список `[{name: key, value: val}, ...]` (kubeadm 1.31+)
- Значения всегда приводятся к строке

#### `check_denylist(layer, denylist)`
- Принимает dict (role или node layer) и список запрещённых путей (dot-notation: `kubelet.clusterDNS`, `cluster.serviceSubnet`)
- Возвращает список нарушений `[{path: "kubelet.clusterDNS", value: ...}]`
- Пустой список = валидация пройдена

### 2. `defaults.yaml`

```yaml
kubeadm:
  version: ""
  arch: amd64
  download:
    base_url: https://dl.k8s.io/release
  paths:
    install_dir: /opt/kubernetes
    bin_dir: /usr/local/bin
    patches_dir: /etc/kubernetes/patches
    config_dir: /etc/kubernetes/kubeadm
    backup_dir: /etc/kubernetes/salt-backup
    backup_keep: 5
  cluster:
    clusterName: ""
    controlPlaneEndpoint: ""
    networking:
      serviceSubnet: 10.96.0.0/12
      podSubnet: 10.244.0.0/16
      dnsDomain: cluster.local
    apiServer:
      certSANs: []
      extraArgs: {}
    controllerManager:
      extraArgs: {}
    scheduler:
      extraArgs: {}
    etcd:
      local:
        extraArgs: {}
  kubelet:
    cgroupDriver: systemd
    clusterDNS: []
    clusterDomain: cluster.local
  certs:
    auto_renew: false
    threshold_days: 30
  addons:
    kube_proxy:
      managed_by: kubeadm
    coredns:
      managed_by: kubeadm
  node:
    role: ""
    advertise_address: ""
    flags: {}
    labels: {}
    taints: {}
    patches: {}
  roles:
    control-plane:
      taints: {}
      patches: {}
    worker:
      patches: {}
  orchestrate:
    transport: minion
    nodegroups:
      masters: ""
      workers: ""
    source_ip: ~
    decommissioned: []
    health:
      retries: 30
      interval: 10

# Набор ключей, которые хранятся как named lists (dict → list с name)
named_list_keys:
  - containers
  - initContainers
  - env
  - volumes
  - volumeMounts
  - ports

# Denylists: пути запрещённые в role/node слоях
denylist:
  kubelet:
    - clusterDNS
    - clusterDomain
    - cgroupDriver
    - rootDir
    - staticPodPath
  cluster:
    - networking.serviceSubnet
    - networking.podSubnet
    - controlPlaneEndpoint
    - apiServer.extraArgs.oidc-issuer-url
    - apiServer.extraArgs.oidc-client-id
    - apiServer.extraArgs.service-account-issuer
    - apiServer.extraArgs.encryption-provider-config
    - apiServer.extraArgs.audit-policy-file
    - apiServer.extraArgs.audit-log-path
    - apiServer.extraArgs.enable-admission-plugins
    - apiServer.extraArgs.disable-admission-plugins
    - apiServer.extraArgs.authorization-mode
    - apiServer.extraArgs.feature-gates
    - controllerManager.extraArgs.feature-gates
    - scheduler.extraArgs.feature-gates
  etcd:
    - local.extraArgs.name
    - local.extraArgs.initial-cluster
    - local.extraArgs.initial-cluster-state
    - local.extraArgs.initial-advertise-peer-urls
    - local.extraArgs.listen-peer-urls
    - local.extraArgs.heartbeat-interval
    - local.extraArgs.election-timeout
```

### 3. `map.jinja`

- Загружает `defaults.yaml`
- Мержит через `kubeadm_util.merge_layers`
- Вызывает `kubeadm_util.check_denylist` для role и node слоёв
- При нарушении — `raise` с понятным сообщением
- Результат: переменная `kubeadm` доступная во всех SLS

### 4. `macros.jinja`

- Макросы для повторяющихся паттернов (пока минимум, расширяется в следующих фазах)

### 5. Pillar examples

- `pillar.example` с полным примером всех слоёв (cluster, roles, node, orchestrate, addons)

### 6. CI pipeline

- `pytest` для `kubeadm_util.py`
- Lint: `salt-lint` для SLS, `pylint`/`ruff` для Python
- Рендеринг всех SLS с example pillar (проверка что Jinja не падает)

---

## TDD: тесты

### Файл: `test/unit/test_kubeadm_util.py`

```python
"""
Тесты для _modules/kubeadm_util.py

Запуск: pytest test/unit/test_kubeadm_util.py -v
"""
import pytest

# Фикстура: named_list_keys из defaults.yaml
NAMED_LIST_KEYS = [
    "containers", "initContainers", "env",
    "volumes", "volumeMounts", "ports",
]


class TestMergeLayers:
    """merge_layers: рекурсивное слияние defaults → cluster → role → node."""

    def test_empty_layers_return_defaults(self):
        """Пустые слои не меняют defaults."""
        defaults = {"kubelet": {"cgroupDriver": "systemd", "clusterDNS": ["10.96.0.10"]}}
        result = merge_layers(defaults, {}, {}, {})
        assert result == defaults

    def test_cluster_overrides_defaults(self):
        """Cluster слой перезаписывает значения defaults."""
        defaults = {"kubelet": {"cgroupDriver": "systemd", "maxPods": 110}}
        cluster = {"kubelet": {"maxPods": 200}}
        result = merge_layers(defaults, cluster, {}, {})
        assert result["kubelet"]["cgroupDriver"] == "systemd"
        assert result["kubelet"]["maxPods"] == 200

    def test_deep_recursive_merge(self):
        """Вложенные dict мержатся рекурсивно, а не заменяются."""
        defaults = {"cluster": {"apiServer": {"extraArgs": {"a": "1"}, "certSANs": []}}}
        cluster = {"cluster": {"apiServer": {"extraArgs": {"b": "2"}}}}
        result = merge_layers(defaults, cluster, {}, {})
        assert result["cluster"]["apiServer"]["extraArgs"] == {"a": "1", "b": "2"}
        assert result["cluster"]["apiServer"]["certSANs"] == []

    def test_list_replaced_not_merged(self):
        """Списки заменяются целиком, не конкатенируются."""
        defaults = {"kubelet": {"clusterDNS": ["10.96.0.10"]}}
        cluster = {"kubelet": {"clusterDNS": ["10.96.0.20", "10.96.0.21"]}}
        result = merge_layers(defaults, cluster, {}, {})
        assert result["kubelet"]["clusterDNS"] == ["10.96.0.20", "10.96.0.21"]

    def test_none_removes_key(self):
        """None в верхнем слое удаляет ключ из результата."""
        defaults = {"kubelet": {"maxPods": 110, "cgroupDriver": "systemd"}}
        node = {"kubelet": {"maxPods": None}}
        result = merge_layers(defaults, {}, {}, node)
        assert "maxPods" not in result["kubelet"]
        assert result["kubelet"]["cgroupDriver"] == "systemd"

    def test_layer_priority_node_wins(self):
        """Node слой имеет наивысший приоритет."""
        defaults = {"kubelet": {"maxPods": 110}}
        cluster = {"kubelet": {"maxPods": 150}}
        role = {"kubelet": {"maxPods": 200}}
        node = {"kubelet": {"maxPods": 250}}
        result = merge_layers(defaults, cluster, role, node)
        assert result["kubelet"]["maxPods"] == 250

    def test_new_keys_added_from_upper_layers(self):
        """Верхние слои могут добавлять новые ключи."""
        defaults = {"kubelet": {"cgroupDriver": "systemd"}}
        node = {"kubelet": {"customSetting": "value"}}
        result = merge_layers(defaults, {}, {}, node)
        assert result["kubelet"]["customSetting"] == "value"

    def test_does_not_mutate_inputs(self):
        """Входные словари не мутируются."""
        defaults = {"kubelet": {"maxPods": 110}}
        cluster = {"kubelet": {"maxPods": 200}}
        import copy
        defaults_copy = copy.deepcopy(defaults)
        cluster_copy = copy.deepcopy(cluster)
        merge_layers(defaults, cluster, {}, {})
        assert defaults == defaults_copy
        assert cluster == cluster_copy


class TestToK8s:
    """to_k8s: dict-of-dicts → list-of-dicts для named-list keys."""

    def test_containers_dict_to_list(self):
        """containers как dict конвертируется в list с name."""
        obj = {
            "spec": {
                "containers": {
                    "nginx": {"image": "nginx:1.25"},
                    "sidecar": {"image": "sidecar:1.0"},
                }
            }
        }
        result = to_k8s(obj, NAMED_LIST_KEYS)
        containers = result["spec"]["containers"]
        assert isinstance(containers, list)
        assert len(containers) == 2
        names = {c["name"] for c in containers}
        assert names == {"nginx", "sidecar"}
        nginx = next(c for c in containers if c["name"] == "nginx")
        assert nginx["image"] == "nginx:1.25"

    def test_env_dict_to_list(self):
        """env как dict конвертируется в list с name."""
        obj = {"env": {"GOMAXPROCS": {"value": "4"}, "DEBUG": {"value": "true"}}}
        result = to_k8s(obj, NAMED_LIST_KEYS)
        assert isinstance(result["env"], list)
        assert len(result["env"]) == 2

    def test_nested_named_lists(self):
        """Named lists внутри named lists конвертируются рекурсивно."""
        obj = {
            "spec": {
                "containers": {
                    "app": {
                        "image": "app:1.0",
                        "env": {"FOO": {"value": "bar"}},
                        "ports": {"http": {"containerPort": 8080}},
                    }
                }
            }
        }
        result = to_k8s(obj, NAMED_LIST_KEYS)
        container = result["spec"]["containers"][0]
        assert container["name"] == "app"
        assert isinstance(container["env"], list)
        assert container["env"][0]["name"] == "FOO"
        assert isinstance(container["ports"], list)
        assert container["ports"][0]["name"] == "http"

    def test_non_named_keys_unchanged(self):
        """Обычные dict не конвертируются в list."""
        obj = {"metadata": {"labels": {"app": "nginx"}}, "spec": {"replicas": 3}}
        result = to_k8s(obj, NAMED_LIST_KEYS)
        assert result == obj

    def test_already_list_unchanged(self):
        """Если named key уже list — не трогаем."""
        obj = {"spec": {"containers": [{"name": "app", "image": "app:1.0"}]}}
        result = to_k8s(obj, NAMED_LIST_KEYS)
        assert result["spec"]["containers"] == [{"name": "app", "image": "app:1.0"}]

    def test_empty_dict_to_empty_list(self):
        """Пустой dict named key → пустой list."""
        obj = {"spec": {"containers": {}}}
        result = to_k8s(obj, NAMED_LIST_KEYS)
        assert result["spec"]["containers"] == []

    def test_does_not_mutate_input(self):
        """Входной объект не мутируется."""
        import copy
        obj = {"spec": {"containers": {"app": {"image": "app:1.0"}}}}
        obj_copy = copy.deepcopy(obj)
        to_k8s(obj, NAMED_LIST_KEYS)
        assert obj == obj_copy


class TestExtraArgs:
    """extra_args: dict → map (v1beta3) или list (v1beta4)."""

    def test_v1beta3_returns_dict(self):
        """v1beta3: возвращает dict, значения как строки."""
        args = {"audit-log-maxage": 30, "enable-admission-plugins": "NodeRestriction"}
        result = extra_args(args, "v1beta3")
        assert isinstance(result, dict)
        assert result["audit-log-maxage"] == "30"
        assert result["enable-admission-plugins"] == "NodeRestriction"

    def test_v1beta4_returns_list(self):
        """v1beta4: возвращает list of {name, value}."""
        args = {"flag-a": "val-a", "flag-b": 42}
        result = extra_args(args, "v1beta4")
        assert isinstance(result, list)
        assert len(result) == 2
        names = {item["name"] for item in result}
        assert names == {"flag-a", "flag-b"}
        flag_b = next(i for i in result if i["name"] == "flag-b")
        assert flag_b["value"] == "42"

    def test_empty_args_v1beta3(self):
        result = extra_args({}, "v1beta3")
        assert result == {}

    def test_empty_args_v1beta4(self):
        result = extra_args({}, "v1beta4")
        assert result == []

    def test_bool_values_to_string(self):
        """Boolean значения приводятся к строке."""
        result = extra_args({"flag": True}, "v1beta3")
        assert result["flag"] == "true"

    def test_unknown_version_raises(self):
        """Неизвестная api version вызывает ValueError."""
        with pytest.raises(ValueError, match="api_version"):
            extra_args({"a": "b"}, "v1beta99")


class TestCheckDenylist:
    """check_denylist: находит запрещённые пути в слое."""

    def test_no_violations(self):
        """Чистый слой без запрещённых ключей."""
        layer = {"kubelet": {"maxPods": 200}}
        denylist = {"kubelet": ["clusterDNS", "cgroupDriver"]}
        result = check_denylist(layer, denylist)
        assert result == []

    def test_single_violation(self):
        """Один запрещённый ключ."""
        layer = {"kubelet": {"clusterDNS": ["10.0.0.10"]}}
        denylist = {"kubelet": ["clusterDNS"]}
        result = check_denylist(layer, denylist)
        assert len(result) == 1
        assert result[0]["path"] == "kubelet.clusterDNS"

    def test_multiple_violations(self):
        """Несколько нарушений возвращаются все."""
        layer = {
            "kubelet": {"clusterDNS": ["10.0.0.10"], "cgroupDriver": "cgroupfs"},
        }
        denylist = {"kubelet": ["clusterDNS", "cgroupDriver"]}
        result = check_denylist(layer, denylist)
        paths = {v["path"] for v in result}
        assert paths == {"kubelet.clusterDNS", "kubelet.cgroupDriver"}

    def test_nested_dotpath(self):
        """Dot-notation для вложенных путей."""
        layer = {"cluster": {"networking": {"serviceSubnet": "10.0.0.0/8"}}}
        denylist = {"cluster": ["networking.serviceSubnet"]}
        result = check_denylist(layer, denylist)
        assert len(result) == 1
        assert result[0]["path"] == "cluster.networking.serviceSubnet"

    def test_empty_layer(self):
        denylist = {"kubelet": ["clusterDNS"]}
        assert check_denylist({}, denylist) == []

    def test_empty_denylist(self):
        layer = {"kubelet": {"clusterDNS": ["10.0.0.10"]}}
        assert check_denylist(layer, {}) == []

    def test_violation_includes_value(self):
        """Результат содержит значение нарушающего ключа для диагностики."""
        layer = {"kubelet": {"clusterDNS": ["custom"]}}
        denylist = {"kubelet": ["clusterDNS"]}
        result = check_denylist(layer, denylist)
        assert result[0]["value"] == ["custom"]
```

### Файл: `test/unit/test_map_jinja.py`

```python
"""
Тесты рендеринга map.jinja через Salt renderer.

Запуск: pytest test/unit/test_map_jinja.py -v
Требует: saltstack установлен, либо mock Salt renderer.
"""
import pytest


class TestMapJinja:
    """map.jinja: слияние и валидация pillar → переменная kubeadm."""

    def test_renders_with_minimal_pillar(self, salt_render):
        """Минимальный pillar рендерится без ошибок."""
        pillar = {
            "kubeadm": {
                "version": "v1.25.16",
                "cluster": {
                    "clusterName": "test",
                    "controlPlaneEndpoint": "k8s-api:6443",
                },
                "node": {"role": "worker"},
            }
        }
        result = salt_render("map.jinja", pillar)
        assert result["version"] == "v1.25.16"
        assert result["cluster"]["clusterName"] == "test"
        # defaults applied
        assert result["kubelet"]["cgroupDriver"] == "systemd"

    def test_node_layer_merged(self, salt_render):
        """Node layer мержится поверх role и cluster."""
        pillar = {
            "kubeadm": {
                "version": "v1.25.16",
                "cluster": {"clusterName": "test", "controlPlaneEndpoint": "k8s:6443"},
                "roles": {"worker": {"patches": {"kubeletconfiguration": {"maxPods": 200}}}},
                "node": {
                    "role": "worker",
                    "patches": {"kubeletconfiguration": {"maxPods": 250}},
                },
            }
        }
        result = salt_render("map.jinja", pillar)
        assert result["node"]["patches"]["kubeletconfiguration"]["maxPods"] == 250

    def test_denylist_raises_on_violation(self, salt_render):
        """Запрещённый ключ в node layer вызывает ошибку рендеринга."""
        pillar = {
            "kubeadm": {
                "version": "v1.25.16",
                "cluster": {"clusterName": "test", "controlPlaneEndpoint": "k8s:6443"},
                "node": {
                    "role": "worker",
                    "kubelet": {"clusterDNS": ["10.0.0.10"]},  # denied
                },
            }
        }
        # map.jinja должен вызвать ошибку рендеринга
        with pytest.raises(Exception, match="clusterDNS"):
            salt_render("map.jinja", pillar)

    def test_denylist_cluster_subnet(self, salt_render):
        """serviceSubnet в node layer запрещён."""
        pillar = {
            "kubeadm": {
                "version": "v1.25.16",
                "cluster": {"clusterName": "test", "controlPlaneEndpoint": "k8s:6443"},
                "node": {
                    "role": "control-plane",
                    "cluster": {"networking": {"serviceSubnet": "10.0.0.0/8"}},
                },
            }
        }
        with pytest.raises(Exception, match="serviceSubnet"):
            salt_render("map.jinja", pillar)
```

---

## TDD: порядок работы

### Шаг 1 — Пишем тесты
1. Создать `test/unit/test_kubeadm_util.py` с тестами выше
2. Создать `test/unit/conftest.py` с фикстурами (import path для `_modules`)
3. Запустить: `pytest test/unit/ -v` — все тесты FAIL (модуль не существует)

### Шаг 2 — Валидация тестов
1. Проверить что тесты корректно импортируют будущий модуль
2. Проверить что каждый тест проверяет ровно одно поведение
3. Проверить edge cases: пустые входы, None, мутация

### Шаг 3 — Реализация
1. `_modules/kubeadm_util.py` — все 4 функции
2. `defaults.yaml` — defaults, named_list_keys, denylist
3. `map.jinja` — загрузка, мерж, валидация
4. `macros.jinja` — минимальный набор
5. `pillar.example`

### Шаг 4 — Прогон тестов
1. `pytest test/unit/test_kubeadm_util.py -v` — все GREEN
2. `salt-lint kubeadm/*.sls`
3. Рендеринг SLS с example pillar: `salt-call --local slsutil.renderer kubeadm/init.sls`

---

## Acceptance criteria

- [ ] `merge_layers` корректно мержит 4 слоя, результат совпадает с ожидаемым YAML для role и node overrides
- [ ] `to_k8s` преобразует named lists включая вложенные (containers → env → ports)
- [ ] `extra_args` выдаёт dict для v1beta3 и list для v1beta4, все значения — строки
- [ ] `check_denylist` находит нарушения по dot-notation путям
- [ ] Запрещённый ключ в node pillar (`clusterDNS`, `serviceSubnet`) вызывает ошибку при рендеринге map.jinja с понятным сообщением
- [ ] Все тесты проходят: `pytest test/unit/ -v`
- [ ] Lint чистый: `salt-lint`, `ruff check`
