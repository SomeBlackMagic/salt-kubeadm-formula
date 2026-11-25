# Phase 7 — Certificates

## Цель

Автоматическое и ручное обновление сертификатов компонентов Kubernetes. Renew script с safety gate, systemd timer, orchestrate.

## Зависимости

- Phase 2: healthy state, pick_source
- Phase 4: etcd checks
- Phase 6: reconfigure (restart logic)

## Что реализуем

### 1. Renew script: `/usr/local/bin/kubeadm-certs-renew`

Bash-скрипт (или Python, для тестируемости), единая реализация для timer и orch.

**Режимы:**
- `--check`: только проверка expiry, exit 0 если всё ОК, exit 1 если что-то < threshold
- `--force`: обновить независимо от threshold
- `--threshold-days N`: порог (default 30)

**Алгоритм:**

```
1. kubeadm certs check-expiration --config <local config>
   - Local config чтобы проверка работала даже при expired apiserver cert

2. Если days_left > threshold и не --force → exit 0 (ничего не делать)

3. Safety gate:
   - etcd /health на localhost:2381 (http.query pattern)
   - /readyz на localhost:6443
   - Любой fail → exit 1 WITHOUT changes
   - Это заменяет orchestration для timer: broken master-1 блокирует master-2

4. Backup:
   - cp -a /etc/kubernetes/pki → /etc/kubernetes/pki.bak.<timestamp>
   - cp -a /etc/kubernetes/*.conf → backup dir

5. kubeadm certs renew all --config <local config>
   - SANs сохраняются из существующих сертификатов

6. Restart static pods (по одному, с проверкой):
   a. etcd:
      - mv /etc/kubernetes/manifests/etcd.yaml /tmp/
      - Wait: pod disappears (kubectl get pod)
      - mv /tmp/etcd.yaml /etc/kubernetes/manifests/
      - Wait: etcd /health OK
   b. kube-apiserver:
      - Same move trick
      - Wait: /readyz OK
   c. kube-controller-manager:
      - Same
   d. kube-scheduler:
      - Same

7. Verify:
   - New expiry > old expiry
   - etcd /health
   - /readyz

8. On failure:
   - Restore from backup
   - Restart components (same move trick)
   - Works only while OLD certs still valid (hence 30-day margin)
   - Exit 1
```

### 2. `kubeadm/certs/init.sls`

```yaml
# Renew script
kubeadm_certs_renew_script:
  file.managed:
    - name: /usr/local/bin/kubeadm-certs-renew
    - source: salt://kubeadm/files/kubeadm-certs-renew
    - mode: '0755'
    - template: jinja

# Systemd service (oneshot)
kubeadm_certs_renew_service:
  file.managed:
    - name: /etc/systemd/system/kubeadm-certs-renew.service
    - source: salt://kubeadm/files/kubeadm-certs-renew.service
    - template: jinja

# Systemd timer (staggered by master index)
kubeadm_certs_renew_timer:
  file.managed:
    - name: /etc/systemd/system/kubeadm-certs-renew.timer
    - contents: |
        [Unit]
        Description=Kubeadm certificate renewal timer

        [Timer]
        OnCalendar=Mon *-*-1..7 03:{{ master_index }}0:00
        Persistent=true

        [Install]
        WantedBy=timers.target

{% if certs.auto_renew %}
kubeadm_certs_timer_enabled:
  service.running:
    - name: kubeadm-certs-renew.timer
    - enable: True
{% else %}
kubeadm_certs_timer_disabled:
  service.dead:
    - name: kubeadm-certs-renew.timer
    - enable: False
{% endif %}
```

### 3. `kubeadm/certs/check.sls`

```yaml
# Запускает script в --check режиме
# Для Salt scheduler на masters
kubeadm_certs_check:
  module.run:
    - name: cmd.run
    - cmd: /usr/local/bin/kubeadm-certs-renew --check --threshold-days {{ threshold_days }}
```

> Здесь cmd.run оправдан: вызов внешнего скрипта

### 4. `_states/kubeadm.py` — дополнение

#### `kubeadm.certs_renewed`
- **Apply**: запускает renew script
- **test=True**: expiry table, что будет обновлено, restart order
- Returns: old expiry, new expiry, components restarted

### 5. `_runners/kubeadm.py` — дополнение

#### `cert_expiry(nodegroup, transport)` — read-only
- `kubeadm certs check-expiration` на каждом master
- Возвращает таблицу: `{node: [{cert, expiry, days_left}, ...]}`

### 6. `orch/kubeadm/certs.sls`

```
1. cert_expiry на всех masters → event с таблицей
2. Для каждого master последовательно:
   a. etcd quorum check (все members healthy)
   b. salt.state kubeadm.certs с force/threshold из pillar
   c. Health: /readyz, etcd health, all members healthy
   d. Event kubeadm/certs/done/<node>
```

---

## TDD: тесты

### Файл: `test/unit/test_certs_renew_script.py`

```python
"""
Тесты для renew script логики.
Если скрипт на Python — прямые unit тесты.
Если bash — тесты через subprocess mock или integration.
"""
import pytest


class TestRenewLogic:
    """Логика обновления сертификатов."""

    def test_check_mode_no_changes(self):
        """--check режим: только проверка, без изменений."""
        # Script exit code 0 если все certs OK
        # Script exit code 1 если что-то < threshold
        pass

    def test_skip_if_not_expiring(self):
        """Certs не истекают скоро → skip (exit 0)."""
        pass

    def test_force_ignores_threshold(self):
        """--force обновляет независимо от threshold."""
        pass

    def test_safety_gate_blocks_on_unhealthy_etcd(self):
        """Unhealthy etcd → exit 1 без изменений."""
        pass

    def test_safety_gate_blocks_on_readyz_fail(self):
        """/readyz fail → exit 1 без изменений."""
        pass

    def test_backup_created_before_renew(self):
        """Backup pki и *.conf создаются до renew."""
        pass

    def test_restart_order_etcd_first(self):
        """Restart order: etcd → apiserver → cm → scheduler."""
        pass

    def test_rollback_on_failure(self):
        """Failure → restore from backup."""
        pass


class TestCertsState:
    """State kubeadm.certs_renewed."""

    def test_test_mode_shows_expiry(self, state_mod):
        """test=True → expiry table и что будет обновлено."""
        state_mod.__opts__["test"] = True
        state_mod.__salt__["cmd.run_all"] = MagicMock(return_value={
            "retcode": 0,
            "stdout": "CERTIFICATE                EXPIRES                  RESIDUAL TIME\n"
                      "apiserver                  Jan 01, 2027 00:00 UTC   50d\n"
        })
        result = state_mod.certs_renewed(
            name="certs", threshold_days=30
        )
        assert result["result"] is None
        assert "apiserver" in result["comment"]

    def test_apply_runs_script(self, state_mod):
        """Apply → запускает renew script."""
        state_mod.__salt__["cmd.run_all"] = MagicMock(return_value={
            "retcode": 0, "stdout": "renewed", "stderr": ""
        })
        result = state_mod.certs_renewed(name="certs")
        assert result["result"] is True
        assert state_mod.__salt__["cmd.run_all"].called


class TestCertExpiry:
    """Runner kubeadm.cert_expiry."""

    def test_returns_expiry_table(self, runner_mod):
        """Возвращает expiry per node per cert."""
        runner_mod._cmd = MagicMock(return_value={
            "master-1": "CERTIFICATE      EXPIRES                  RESIDUAL TIME\n"
                        "apiserver        Jan 01, 2027 00:00 UTC   50d\n"
                        "etcd-server      Jan 01, 2027 00:00 UTC   50d\n",
            "master-2": "CERTIFICATE      EXPIRES                  RESIDUAL TIME\n"
                        "apiserver        Feb 01, 2027 00:00 UTC   81d\n",
        })
        result = runner_mod.cert_expiry(nodegroup="k8s-masters", transport="minion")
        assert "master-1" in result
        assert "master-2" in result
        assert any(c["cert"] == "apiserver" for c in result["master-1"])
```

### Файл: `test/unit/test_certs_sls.py`

```python
"""
Тесты рендеринга kubeadm/certs/init.sls.
"""
import pytest


class TestCertsSLS:
    """kubeadm/certs/init.sls."""

    def test_script_installed(self, render_sls):
        """Renew script устанавливается."""
        states = render_sls("kubeadm/certs/init.sls", minimal_pillar())
        scripts = [s for s in states
                   if s["fun"] == "file.managed" and "kubeadm-certs-renew" in s["name"]]
        assert len(scripts) >= 1
        assert scripts[0]["mode"] == "0755"

    def test_timer_staggered(self, render_sls):
        """Timer staggered по master index."""
        pillar = minimal_pillar()
        pillar["kubeadm"]["_master_index"] = 2
        states = render_sls("kubeadm/certs/init.sls", pillar)
        timer = next(s for s in states if "timer" in s["name"] and s["fun"] == "file.managed")
        content = timer.get("contents", "")
        assert "03:20" in content  # index 2 → 03:20

    def test_auto_renew_true_enables_timer(self, render_sls):
        """auto_renew: true → timer enabled."""
        pillar = minimal_pillar()
        pillar["kubeadm"]["certs"]["auto_renew"] = True
        states = render_sls("kubeadm/certs/init.sls", pillar)
        enabled = [s for s in states
                   if s.get("fun") == "service.running"
                   and "timer" in s["name"]]
        assert len(enabled) >= 1

    def test_auto_renew_false_disables_timer(self, render_sls):
        """auto_renew: false → timer disabled."""
        pillar = minimal_pillar()
        pillar["kubeadm"]["certs"]["auto_renew"] = False
        states = render_sls("kubeadm/certs/init.sls", pillar)
        disabled = [s for s in states
                    if s.get("fun") == "service.dead"
                    and "timer" in s["name"]]
        assert len(disabled) >= 1
```

### Файл: `test/unit/test_orch_certs.py`

```python
"""
Тесты для orch/kubeadm/certs.sls.
"""
import pytest


class TestOrchCerts:
    """Orchestrate certificate renewal."""

    def test_expiry_table_first(self, render_orch):
        """Первый шаг: cert_expiry → event."""
        states = render_orch("orch/kubeadm/certs.sls", orch_pillar())
        first = states[0]
        assert "cert_expiry" in str(first) or "expiry" in str(first).lower()

    def test_sequential_per_master(self, render_orch):
        """Masters обрабатываются последовательно."""
        states = render_orch("orch/kubeadm/certs.sls", orch_pillar())
        cert_steps = [s for s in states if "certs" in str(s.get("sls", ""))]
        for i in range(1, len(cert_steps)):
            assert cert_steps[i].get("require") is not None

    def test_etcd_quorum_check_per_master(self, render_orch):
        """etcd quorum check перед каждым master."""
        states = render_orch("orch/kubeadm/certs.sls", orch_pillar())
        for i, s in enumerate(states):
            if "certs" in str(s.get("sls", "")):
                # Перед этим шагом должен быть etcd check
                prev_steps = states[max(0, i-3):i]
                assert any("etcd" in str(p) for p in prev_steps)
```

---

## TDD: порядок работы

### Шаг 1 — Тесты
1. `test/unit/test_certs_renew_script.py`
2. `test/unit/test_certs_sls.py`
3. `test/unit/test_orch_certs.py`

### Шаг 2 — Валидация
- Safety gate блокирует при unhealthy etcd/apiserver
- Backup before renew, rollback on failure
- Timer staggered по master index
- Sequential per master с etcd quorum check

### Шаг 3 — Реализация
1. `kubeadm/files/kubeadm-certs-renew` (script)
2. `kubeadm/files/kubeadm-certs-renew.service`
3. `kubeadm/certs/init.sls`
4. `kubeadm/certs/check.sls`
5. `_states/kubeadm.py`: `certs_renewed`
6. `_runners/kubeadm.py`: `cert_expiry`
7. `orch/kubeadm/certs.sls`

### Шаг 4 — Тестирование
1. Unit: `pytest test/unit/ -v`
2. Forced renewal на 3 masters: кластер healthy throughout
3. Timer run пропускает renewal при unhealthy etcd
4. Expiry event emitted by scheduled check

---

## Acceptance criteria

- [ ] Forced renewal на 3 masters последовательно; кластер healthy throughout
- [ ] Timer run пропускает renewal при unhealthy etcd
- [ ] Expiry event emitted scheduled check
- [ ] Safety gate: unhealthy etcd → exit без изменений
- [ ] Backup создаётся до renew; failure → restore
- [ ] Restart order: etcd → apiserver → cm → scheduler
- [ ] Timer staggered по master index
- [ ] `auto_renew: false` → timer disabled
- [ ] `test=True` показывает expiry table и что будет обновлено
- [ ] `kubelet.conf` НЕ обновляется (client cert rotation by kubelet)
