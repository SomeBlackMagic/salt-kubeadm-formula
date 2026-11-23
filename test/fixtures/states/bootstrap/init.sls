{#- Persistent Debian 13 fixture shared by minion and salt-ssh acceptance tests. #}
{%- set salt_bindir = '/opt/saltstack/salt' %}

bootstrap_fixture_packages:
  pkg.installed:
    - pkgs:
      - haproxy
      - openssh-server

{%- for binary in ['salt', 'salt-key', 'salt-master', 'salt-run', 'salt-ssh'] %}
bootstrap_fixture_{{ binary | replace('-', '_') }}:
  file.symlink:
    - name: /usr/local/bin/{{ binary }}
    - target: {{ salt_bindir }}/{{ binary }}
    - force: True
{%- endfor %}

bootstrap_fixture_master_config:
  file.managed:
    - name: /etc/salt/master.d/kitchen.conf
    - source: salt://test/fixtures/states/bootstrap/files/master.conf
    - makedirs: True

bootstrap_fixture_minion_config:
  file.managed:
    - name: /etc/salt/minion.d/kitchen.conf
    - source: salt://test/fixtures/states/bootstrap/files/minion.conf
    - makedirs: True

bootstrap_fixture_roster:
  file.managed:
    - name: /etc/salt/roster
    - source: salt://test/fixtures/states/bootstrap/files/roster
    - mode: '0600'

bootstrap_fixture_haproxy_config:
  file.managed:
    - name: /etc/haproxy/haproxy.cfg
    - source: salt://test/fixtures/states/bootstrap/files/haproxy.cfg
    - require:
      - pkg: bootstrap_fixture_packages

bootstrap_fixture_master_unit:
  file.managed:
    - name: /etc/systemd/system/salt-master.service
    - source: salt://test/fixtures/states/bootstrap/files/salt-master.service

bootstrap_fixture_event_listener_unit:
  file.managed:
    - name: /etc/systemd/system/kubeadm-event-listener.service
    - source: salt://test/fixtures/states/bootstrap/files/kubeadm-event-listener.service

bootstrap_fixture_sshd_config:
  file.managed:
    - name: /etc/ssh/sshd_config.d/kubeadm-kitchen.conf
    - source: salt://test/fixtures/states/bootstrap/files/sshd.conf
    - makedirs: True
    - require:
      - pkg: bootstrap_fixture_packages

bootstrap_fixture_ssh_directory:
  file.directory:
    - name: /root/.ssh
    - mode: '0700'

bootstrap_fixture_ssh_key:
  cmd.run:
    - name: ssh-keygen -q -t ed25519 -N '' -f /root/.ssh/kitchen
    - creates: /root/.ssh/kitchen
    - require:
      - file: bootstrap_fixture_ssh_directory

bootstrap_fixture_authorized_key:
  cmd.run:
    - name: install -m 0600 /root/.ssh/kitchen.pub /root/.ssh/authorized_keys
    - unless: cmp -s /root/.ssh/kitchen.pub /root/.ssh/authorized_keys
    - require:
      - cmd: bootstrap_fixture_ssh_key

bootstrap_fixture_systemd_reload:
  module.run:
    - name: service.systemctl_reload
    - onchanges:
      - file: bootstrap_fixture_master_unit
      - file: bootstrap_fixture_event_listener_unit

bootstrap_fixture_master_service:
  service.running:
    - name: salt-master
    - enable: True
    - watch:
      - file: bootstrap_fixture_master_config
      - file: bootstrap_fixture_master_unit
    - require:
      - module: bootstrap_fixture_systemd_reload

bootstrap_fixture_minion_service:
  service.running:
    - name: salt-minion
    - enable: True
    - watch:
      - file: bootstrap_fixture_minion_config
    - require:
      - service: bootstrap_fixture_master_service

bootstrap_fixture_ssh_service:
  service.running:
    - name: ssh
    - enable: True
    - watch:
      - file: bootstrap_fixture_sshd_config
      - cmd: bootstrap_fixture_authorized_key

bootstrap_fixture_haproxy_service:
  service.running:
    - name: haproxy
    - enable: True
    - watch:
      - file: bootstrap_fixture_haproxy_config

bootstrap_fixture_accept_key:
  cmd.run:
    - name: /usr/local/bin/salt-key -A -y
    - require:
      - service: bootstrap_fixture_master_service
      - service: bootstrap_fixture_minion_service

bootstrap_fixture_minion_ping:
  cmd.run:
    - name: /usr/local/bin/salt --timeout=10 master-1 test.ping
    - retry:
        attempts: 15
        interval: 2
        until: True
    - require:
      - cmd: bootstrap_fixture_accept_key

bootstrap_fixture_sync_minion:
  cmd.run:
    - name: /usr/local/bin/salt master-1 saltutil.sync_all
    - require:
      - cmd: bootstrap_fixture_minion_ping

bootstrap_fixture_sync_runners:
  cmd.run:
    - name: /usr/local/bin/salt-run saltutil.sync_runners
    - require:
      - service: bootstrap_fixture_master_service

bootstrap_fixture_ssh_ping:
  cmd.run:
    - name: /usr/local/bin/salt-ssh -i master-1 test.ping
    - require:
      - service: bootstrap_fixture_ssh_service
      - file: bootstrap_fixture_roster
