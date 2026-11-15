#!/usr/bin/env bash
set -euo pipefail

mode="${1:?usage: reset.sh <minion|ssh>}"
artifacts=/tmp/kubeadm-acceptance

if [[ "${mode}" == "ssh" ]]; then
  salt-ssh -i master-1 state.apply kubeadm.reset \
    pillar='{"kubeadm":{"confirm_reset":true}}' \
    >"${artifacts}/reset-confirmed.log" 2>&1
else
  salt master-1 state.apply kubeadm.reset \
    pillar='{"kubeadm":{"confirm_reset":true}}' \
    >"${artifacts}/reset-confirmed.log" 2>&1
fi

test ! -e /etc/kubernetes
test ! -e /var/lib/etcd
test ! -e /etc/cni/net.d
