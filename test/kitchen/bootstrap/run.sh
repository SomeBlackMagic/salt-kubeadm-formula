#!/usr/bin/env bash
set -euo pipefail

mode="${1:?usage: run.sh <minion|ssh>}"
artifacts=/tmp/kubeadm-acceptance
mkdir -p "${artifacts}"
printf '%s\n' "${mode}" >"${artifacts}/transport"

run_orchestrate() {
  salt-run state.orchestrate \
    orch.kubeadm.bootstrap saltenv=base pillarenv=base "$@"
}

run_target() {
  if [[ "${mode}" == "ssh" ]]; then
    salt-ssh -i master-1 "$@"
  else
    salt master-1 "$@"
  fi
}

test ! -e /etc/kubernetes/admin.conf
test ! -e /etc/kubernetes/manifests/kube-apiserver.yaml

run_orchestrate test=True >"${artifacts}/test-mode.log" 2>&1
grep -q 'InitConfiguration' "${artifacts}/test-mode.log"
test ! -e /etc/kubernetes/admin.conf
test ! -e /etc/kubernetes/manifests/kube-apiserver.yaml

if run_target state.apply kubeadm.reset \
  >"${artifacts}/reset-refused.log" 2>&1; then
  echo 'reset without confirmation unexpectedly succeeded' >&2
  exit 1
fi
grep -q 'confirm_reset' "${artifacts}/reset-refused.log"

systemctl stop haproxy
if run_orchestrate >"${artifacts}/lb-refused.log" 2>&1; then
  echo 'bootstrap with an unavailable endpoint unexpectedly succeeded' >&2
  exit 1
fi
grep -q 'endpoint_precheck' "${artifacts}/lb-refused.log"
test ! -e /etc/kubernetes/admin.conf
systemctl start haproxy

timeout 300 salt-run state.event \
  tagmatch='kubeadm/bootstrap/done/*' count=1 \
  >"${artifacts}/event.log" 2>&1 &
event_pid=$!
sleep 2

run_orchestrate >"${artifacts}/bootstrap.log" 2>&1
wait "${event_pid}"
grep -q 'kubeadm/bootstrap/done/master-1' "${artifacts}/event.log"

if run_orchestrate >"${artifacts}/repeat-refused.log" 2>&1; then
  echo 'repeated bootstrap unexpectedly succeeded' >&2
  exit 1
fi
grep -qi 'cluster exists' "${artifacts}/repeat-refused.log"
