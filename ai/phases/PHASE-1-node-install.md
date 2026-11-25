# Phase 1 — Node Install

## Цель

Подготовка узла: скачивание бинарников Kubernetes, настройка systemd unit для kubelet, рендеринг kubeadm конфигов и патчей. После этой фазы узел готов к `kubeadm init/join`, но кластер ещё не создан.

## Зависимости

- Phase 0: `map.jinja`, `defaults.yaml`, `kubeadm_util.py`

## Что реализуем

### 1. `kubeadm/binaries.sls`

Скачивание и установка `kubeadm`, `kubelet`, `kubectl`.

**Логика:**
- Бинарники скачиваются из `kubeadm:download:base_url` для версии `kubeadm:version` и архитектуры `kubeadm:arch`
- URL: `{base_url}/{version}/bin/linux/{arch}/{binary}`
- SHA256 скачивается отдельно: `{url}.sha256`
- Установка в версионированный каталог: `{install_dir}/{version}/`
- Используем `file.managed` с `source_hash` — **не cmd.run**
- Симлинки в `bin_dir` (`/usr/local/bin/`) создаются **только на новом узле** (нет ни одного симлинка kubelet/kubeadm/kubectl)
- На существующем узле — только скачивание, симлинки не трогаем (переключение — только через upgrade)
- Старые версии: оставляем последние 2 версионированных каталога, остальные удаляем

**States (без cmd.run):**
```yaml
# Для каждого бинарника (kubeadm, kubelet, kubectl):
kubeadm_binary_{name}:
  file.managed:
    - name: {install_dir}/{version}/{name}
    - source: {base_url}/{version}/bin/linux/{arch}/{name}
    - source_hash: {base_url}/{version}/bin/linux/{arch}/{name}.sha256
    - makedirs: True
    - mode: '0755'

# Симлинки (только на новом узле — unless проверяет наличие):
kubeadm_symlink_{name}:
  file.symlink:
    - name: {bin_dir}/{name}
    - target: {install_dir}/{version}/{name}
    - unless:
      - test -L {bin_dir}/{name}  # симлинк уже есть — не трогаем
    - require:
      - file: kubeadm_binary_{name}

# Очистка старых версий через custom state или file.directory + Jinja
```

### 2. `kubeadm/kubelet.sls`

Systemd unit и конфигурация kubelet.

**Файлы:**
- `files/kubelet.service` — основной unit
- `files/10-kubeadm.conf` — drop-in с `EnvironmentFile` для `kubeadm-flags.env` и `/etc/default/kubelet`, `--config=/var/lib/kubelet/config.yaml`
- `/etc/default/kubelet` — `KUBELET_EXTRA_ARGS` из merged `flags`

**States:**
```yaml
kubelet_unit:
  file.managed:
    - name: /etc/systemd/system/kubelet.service
    - source: salt://kubeadm/files/kubelet.service
    - template: jinja

kubelet_dropin:
  file.managed:
    - name: /etc/systemd/system/kubelet.service.d/10-kubeadm.conf
    - source: salt://kubeadm/files/10-kubeadm.conf
    - template: jinja
    - makedirs: True

kubelet_defaults:
  file.managed:
    - name: /etc/default/kubelet
    - contents: |
        KUBELET_EXTRA_ARGS="{{ flags_string }}"

kubelet_service:
  service.enabled:
    - name: kubelet
    # НЕ service.running: kubelet стартует после kubeadm init/join
    - require:
      - file: kubelet_unit
      - file: kubelet_dropin

# daemon-reload через module.run при изменении unit/drop-in
kubelet_daemon_reload:
  module.run:
    - name: service.systemctl_reload
    - onchanges:
      - file: kubelet_unit
      - file: kubelet_dropin
```

### 3. `kubeadm/config.sls`

Конфигурационные файлы kubeadm и стратегические патчи.

**Конфиги в `{config_dir}/`:**
- `init.yaml` — InitConfiguration + ClusterConfiguration + KubeletConfiguration (для bootstrap)
- `join.yaml` — JoinConfiguration (для join, рендерится при наличии токена)
- `cluster.yaml` — ClusterConfiguration (для reconfigure)
- `kubelet.yaml` — KubeletConfiguration (для reconfigure)

**Патчи в `{patches_dir}/`:**
- `{target}+strategic.yaml` для каждого target из `node.patches`
- Targets: `kubeletconfiguration`, `kube-apiserver`, `kube-controller-manager`, `kube-scheduler`, `etcd`
- Содержимое рендерится через `kubeadm_util.to_k8s()` для преобразования named lists
- `file.directory` с `clean: True` для удаления патчей, убранных из pillar

**States:**
```yaml
kubeadm_config_dir:
  file.directory:
    - name: {config_dir}
    - makedirs: True

kubeadm_patches_dir:
  file.directory:
    - name: {patches_dir}
    - clean: True  # удаляет файлы не управляемые Salt
    - makedirs: True

# Для каждого target в node.patches:
kubeadm_patch_{target}:
  file.managed:
    - name: {patches_dir}/{target}+strategic.yaml
    - contents: {{ patch_content | yaml }}
    - mode: '0600'
    - require:
      - file: kubeadm_patches_dir
    - require_in:
      - file: kubeadm_patches_dir  # для clean: True

# Конфиг-файлы:
kubeadm_init_config:
  file.managed:
    - name: {config_dir}/init.yaml
    - source: salt://kubeadm/files/init.yaml.jinja
    - template: jinja
```

### 4. `kubeadm/init.sls`

Включает все node-level states в правильном порядке:
```yaml
include:
  - kubeadm.binaries
  - kubeadm.kubelet
  - kubeadm.config
```

---

## TDD: тесты

### Файл: `test/unit/test_binaries_render.py`

```python
"""
Тесты рендеринга kubeadm/binaries.sls.

Проверяем что SLS генерирует правильные состояния для разных pillar-конфигов.
"""
import pytest


class TestBinariesSLS:
    """binaries.sls: скачивание и установка бинарников."""

    def test_downloads_three_binaries(self, render_sls):
        """Скачиваются kubeadm, kubelet, kubectl."""
        pillar = minimal_pillar(version="v1.25.16", arch="amd64")
        states = render_sls("kubeadm/binaries.sls", pillar)
        managed_files = [s for s in states if s["fun"] == "file.managed"]
        binary_names = {s["name"].split("/")[-1] for s in managed_files
                       if "/opt/kubernetes/" in s["name"]}
        assert binary_names == {"kubeadm", "kubelet", "kubectl"}

    def test_versioned_install_path(self, render_sls):
        """Бинарники устанавливаются в версионированный каталог."""
        pillar = minimal_pillar(version="v1.25.16")
        states = render_sls("kubeadm/binaries.sls", pillar)
        paths = [s["name"] for s in states
                 if s["fun"] == "file.managed" and "kubeadm" in s["name"]
                 and "/opt/kubernetes/" in s["name"]]
        assert any("/opt/kubernetes/v1.25.16/" in p for p in paths)

    def test_sha256_verification(self, render_sls):
        """source_hash указывает на .sha256 файл."""
        pillar = minimal_pillar(version="v1.25.16")
        states = render_sls("kubeadm/binaries.sls", pillar)
        for s in states:
            if s["fun"] == "file.managed" and "/opt/kubernetes/" in s["name"]:
                assert s.get("source_hash", "").endswith(".sha256")

    def test_symlinks_have_unless_guard(self, render_sls):
        """Симлинки создаются с unless — не перезаписывают существующие."""
        pillar = minimal_pillar(version="v1.25.16")
        states = render_sls("kubeadm/binaries.sls", pillar)
        symlinks = [s for s in states if s["fun"] == "file.symlink"]
        for s in symlinks:
            assert "unless" in s or "onlyif" in s, \
                f"Symlink {s['name']} must have unless guard"

    def test_custom_install_dir(self, render_sls):
        """Кастомный install_dir из pillar используется."""
        pillar = minimal_pillar(version="v1.25.16")
        pillar["kubeadm"]["paths"]["install_dir"] = "/custom/k8s"
        states = render_sls("kubeadm/binaries.sls", pillar)
        paths = [s["name"] for s in states if s["fun"] == "file.managed"]
        assert any("/custom/k8s/" in p for p in paths)

    def test_idempotent_second_run(self, render_sls):
        """Второй запуск с теми же параметрами не меняет ничего."""
        # Проверяется через integration test на реальном узле
        pass
```

### Файл: `test/unit/test_kubelet_render.py`

```python
"""
Тесты рендеринга kubeadm/kubelet.sls.
"""
import pytest


class TestKubeletSLS:
    """kubelet.sls: systemd unit и конфигурация."""

    def test_unit_file_managed(self, render_sls):
        """Создаётся systemd unit файл."""
        states = render_sls("kubeadm/kubelet.sls", minimal_pillar())
        units = [s for s in states
                 if s["fun"] == "file.managed"
                 and "kubelet.service" in s["name"]
                 and ".d/" not in s["name"]]
        assert len(units) == 1

    def test_dropin_file_managed(self, render_sls):
        """Создаётся drop-in файл."""
        states = render_sls("kubeadm/kubelet.sls", minimal_pillar())
        dropins = [s for s in states
                   if s["fun"] == "file.managed" and "10-kubeadm.conf" in s["name"]]
        assert len(dropins) == 1

    def test_defaults_file_with_flags(self, render_sls):
        """KUBELET_EXTRA_ARGS формируется из merged flags."""
        pillar = minimal_pillar()
        pillar["kubeadm"]["node"]["flags"] = {"node-ip": "10.0.0.12"}
        states = render_sls("kubeadm/kubelet.sls", pillar)
        defaults = [s for s in states
                    if s["fun"] == "file.managed" and "/etc/default/kubelet" in s["name"]]
        assert len(defaults) == 1
        content = defaults[0].get("contents", "")
        assert "--node-ip=10.0.0.12" in content

    def test_service_enabled_not_running(self, render_sls):
        """Kubelet enabled, но НЕ running (стартует kubeadm)."""
        states = render_sls("kubeadm/kubelet.sls", minimal_pillar())
        services = [s for s in states if "service" in s["fun"]]
        # Должен быть service.enabled, не service.running
        for s in services:
            if s["name"] == "kubelet":
                assert s["fun"] == "service.enabled"

    def test_daemon_reload_on_unit_change(self, render_sls):
        """daemon-reload запускается при изменении unit или drop-in."""
        states = render_sls("kubeadm/kubelet.sls", minimal_pillar())
        reload_states = [s for s in states
                         if s.get("fun") == "module.run"
                         and "systemctl_reload" in str(s)]
        assert len(reload_states) >= 1
```

### Файл: `test/unit/test_config_render.py`

```python
"""
Тесты рендеринга kubeadm/config.sls.
"""
import pytest


class TestConfigSLS:
    """config.sls: kubeadm конфиги и патчи."""

    def test_config_dir_created(self, render_sls):
        """config_dir создаётся."""
        states = render_sls("kubeadm/config.sls", minimal_pillar())
        dirs = [s for s in states if s["fun"] == "file.directory"]
        config_dirs = [s for s in dirs if "kubeadm" in s["name"]]
        assert len(config_dirs) >= 1

    def test_patches_dir_clean(self, render_sls):
        """patches_dir создаётся с clean: True."""
        states = render_sls("kubeadm/config.sls", minimal_pillar())
        dirs = [s for s in states
                if s["fun"] == "file.directory" and "patches" in s["name"]]
        assert any(s.get("clean") is True for s in dirs)

    def test_patch_file_rendered(self, render_sls):
        """Патч из pillar рендерится в файл."""
        pillar = minimal_pillar()
        pillar["kubeadm"]["node"]["patches"] = {
            "kubeletconfiguration": {"systemReserved": {"cpu": "500m"}}
        }
        states = render_sls("kubeadm/config.sls", pillar)
        patches = [s for s in states
                   if s["fun"] == "file.managed"
                   and "kubeletconfiguration+strategic" in s["name"]]
        assert len(patches) == 1

    def test_patch_mode_0600(self, render_sls):
        """Файлы патчей имеют mode 0600."""
        pillar = minimal_pillar()
        pillar["kubeadm"]["node"]["patches"] = {
            "kube-apiserver": {"spec": {"containers": {"kube-apiserver": {
                "resources": {"requests": {"cpu": "1"}}
            }}}}
        }
        states = render_sls("kubeadm/config.sls", pillar)
        patches = [s for s in states
                   if s["fun"] == "file.managed" and "+strategic" in s["name"]]
        for p in patches:
            assert p.get("mode") in ("0600", "600")

    def test_named_lists_converted_in_patches(self, render_sls):
        """Named lists в патчах конвертируются через to_k8s."""
        pillar = minimal_pillar()
        pillar["kubeadm"]["node"]["patches"] = {
            "kube-apiserver": {
                "spec": {
                    "containers": {
                        "kube-apiserver": {
                            "env": {"GOMAXPROCS": {"value": "4"}}
                        }
                    }
                }
            }
        }
        states = render_sls("kubeadm/config.sls", pillar)
        patch = next(s for s in states
                     if s["fun"] == "file.managed"
                     and "kube-apiserver+strategic" in s["name"])
        # Содержимое должно иметь list формат
        import yaml
        content = yaml.safe_load(patch["contents"])
        containers = content["spec"]["containers"]
        assert isinstance(containers, list)
        assert containers[0]["name"] == "kube-apiserver"

    def test_no_patches_no_files(self, render_sls):
        """Без патчей в pillar — нет файлов патчей."""
        pillar = minimal_pillar()
        pillar["kubeadm"]["node"]["patches"] = {}
        states = render_sls("kubeadm/config.sls", pillar)
        patches = [s for s in states
                   if s["fun"] == "file.managed" and "+strategic" in s["name"]]
        assert patches == []

    def test_removed_patch_cleaned(self):
        """
        Патч удалённый из pillar удаляется с диска (через clean: True).
        Проверяется integration тестом: первый run с патчем, второй без — файл исчез.
        """
        pass
```

### Файл: `test/unit/test_flags_rendering.py`

```python
"""
Тесты формирования KUBELET_EXTRA_ARGS из flags.
"""
import pytest


class TestFlagsRendering:
    """Формирование строки флагов для /etc/default/kubelet."""

    def test_empty_flags(self):
        """Пустые flags → пустая строка."""
        assert render_flags({}) == ""

    def test_single_flag(self):
        assert render_flags({"node-ip": "10.0.0.12"}) == "--node-ip=10.0.0.12"

    def test_multiple_flags_sorted(self):
        """Флаги сортируются для детерминированного вывода."""
        flags = {"node-ip": "10.0.0.12", "hostname-override": "master-1"}
        result = render_flags(flags)
        assert "--hostname-override=master-1" in result
        assert "--node-ip=10.0.0.12" in result
        # Сортировка
        assert result.index("--hostname-override") < result.index("--node-ip")

    def test_boolean_flag_true(self):
        """Boolean True → --flag (без =value)."""
        assert render_flags({"register-node": True}) == "--register-node"

    def test_boolean_flag_false_omitted(self):
        """Boolean False → --flag=false."""
        assert render_flags({"register-node": False}) == "--register-node=false"
```

---

## TDD: порядок работы

### Шаг 1 — Пишем тесты
1. Создать тесты рендеринга SLS (выше)
2. Создать `test/unit/conftest.py` с фикстурами `render_sls`, `minimal_pillar`
3. Запуск: все FAIL

### Шаг 2 — Валидация тестов
1. Убедиться что тесты покрывают: скачивание, sha256, симлинки, unit, drop-in, flags, конфиги, патчи, clean, named lists
2. Убедиться что нет cmd.run в ожидаемых states

### Шаг 3 — Реализация
1. `kubeadm/files/kubelet.service` — шаблон unit
2. `kubeadm/files/10-kubeadm.conf` — шаблон drop-in
3. `kubeadm/binaries.sls`
4. `kubeadm/kubelet.sls`
5. `kubeadm/config.sls`
6. `kubeadm/init.sls`

### Шаг 4 — Прогон тестов
1. Unit тесты: `pytest test/unit/ -v`
2. Lint: `salt-lint kubeadm/*.sls`
3. Идемпотентность: highstate на чистой VM → второй run: 0 changes
4. Версия: изменить `kubeadm:version` → новые бинарники скачаны, симлинки НЕ изменены
5. Патч: удалить из pillar → файл удалён с диска

---

## Acceptance criteria

- [ ] Highstate на свежей VM идемпотентен (второй запуск: 0 изменений)
- [ ] Бинарники скачаны в `/opt/kubernetes/v1.25.16/`, sha256 проверен
- [ ] Симлинки в `/usr/local/bin/` указывают на нужную версию на новом узле
- [ ] Изменение `kubeadm:version` только скачивает новые бинарники; симлинки остаются
- [ ] kubelet.service и drop-in на месте, kubelet enabled но не running
- [ ] `/etc/default/kubelet` содержит `KUBELET_EXTRA_ARGS` из merged flags
- [ ] Патчи рендерятся в `{target}+strategic.yaml`, mode 0600
- [ ] Named lists в патчах конвертированы (`containers` как list)
- [ ] Удалённый из pillar патч удаляется с диска (clean: True)
- [ ] Нигде нет `cmd.run` для скачивания или управления файлами
- [ ] `test=True` показывает diff файлов, не выполняет действий
