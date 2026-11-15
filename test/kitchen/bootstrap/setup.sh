#!/usr/bin/env bash
set -euo pipefail

mode="${1:?usage: setup.sh <minion|ssh>}"
salt_root=/opt/saltstack/salt
master_config=/etc/salt/master.d/kitchen.conf
pillar_file=/tmp/kitchen/srv/pillar/default.sls

if [[ "${mode}" != "minion" && "${mode}" != "ssh" ]]; then
  echo "unsupported transport: ${mode}" >&2
  exit 2
fi

for command in salt salt-master salt-run salt-ssh; do
  ln -sfn "${salt_root}/${command}" "/usr/local/bin/${command}"
done

mkdir -p /etc/salt/master.d /etc/salt/minion.d /var/run/salt/master
cat >"${master_config}" <<'EOF'
interface: 127.0.0.1
auto_accept: true
file_roots:
  base:
    - /tmp/kitchen/srv/salt
pillar_roots:
  base:
    - /tmp/kitchen/srv/pillar
nodegroups:
  k8s-masters: 'L@master-1'
  k8s-workers: 'L@'
roster_file: /etc/salt/roster
EOF

if [[ "${mode}" == "ssh" ]]; then
  sed -i 's/transport: minion/transport: ssh/' "${pillar_file}"
  sed -i '/kube_proxy:/,/managed_by:/s/managed_by: kubeadm/managed_by: external/' \
    "${pillar_file}"

  install -d -m 0700 /root/.ssh
  if [[ ! -f /root/.ssh/kitchen ]]; then
    ssh-keygen -q -t ed25519 -N '' -f /root/.ssh/kitchen
  fi
  cp /root/.ssh/kitchen.pub /root/.ssh/authorized_keys
  chmod 0600 /root/.ssh/authorized_keys
  cat >/etc/salt/roster <<'EOF'
master-1:
  host: 127.0.0.1
  user: root
  priv: /root/.ssh/kitchen
  sudo: false
  tty: false
EOF
else
  cat >/etc/salt/minion.d/kitchen.conf <<'EOF'
master: 127.0.0.1
id: master-1
EOF
fi

cat >/etc/haproxy/haproxy.cfg <<'EOF'
global
  daemon

defaults
  mode tcp
  timeout connect 5s
  timeout client 5m
  timeout server 5m

frontend kubernetes_api
  bind 127.0.0.2:7443
  default_backend kubernetes_api_backend

backend kubernetes_api_backend
  server control_plane 127.0.0.1:6443
EOF

pkill -f '[s]alt-master' 2>/dev/null || true
salt-master -d -c /etc/salt

if [[ "${mode}" == "minion" ]]; then
  systemctl restart salt-minion
  for _attempt in $(seq 1 30); do
    if salt --out=json master-1 test.ping 2>/dev/null | grep -q true; then
      break
    fi
    sleep 1
  done
  salt --out=json master-1 test.ping | grep -q true
  salt master-1 saltutil.sync_all
else
  systemctl restart ssh
  salt-ssh -i --out=json master-1 test.ping | grep -q true
fi

salt-run saltutil.sync_runners
systemctl restart haproxy
