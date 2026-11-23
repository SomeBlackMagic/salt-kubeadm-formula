# -*- coding: utf-8 -*-
# vim: ft=yaml
---
kubeadm:
  version: v1.25.16
  arch: amd64
  download:
    base_url: salt://cache/k8s
  cluster:
    clusterName: kitchen
    controlPlaneEndpoint: k8s-api.test:6443
    networking:
      serviceSubnet: 10.96.0.0/12
      podSubnet: 10.244.0.0/16
      dnsDomain: cluster.local
  kubelet:
    cgroupDriver: systemd
    clusterDNS:
      - 10.96.0.10
    clusterDomain: cluster.local
  roles:
    worker:
      patches:
        kubeletconfiguration:
          maxPods: 200
  node:
    role: worker
    advertise_address: 10.0.0.12
    flags:
      node-ip: 10.0.0.12
    patches: {}
