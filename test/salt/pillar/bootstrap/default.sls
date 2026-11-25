---
containerd:
  components:
    cni:
      enabled: true
    crictl:
      enabled: true
  kernel:
    enabled: true
  config:
    plugins:
      io.containerd.cri.v1.images:
        snapshotter: native
        use_local_image_pull: true

kubeadm:
  version: v1.25.16
  arch: amd64
  download:
    base_url: salt://cache/k8s
  _ignore_preflight_errors:
    - SystemVerification
  cluster:
    clusterName: kitchen
    controlPlaneEndpoint: 127.0.0.2:7443
    networking:
      serviceSubnet: 10.96.0.0/12
      podSubnet: 10.244.0.0/16
      dnsDomain: cluster.local
  kubelet:
    cgroupDriver: systemd
    failSwapOn: false
    clusterDNS:
      - 10.96.0.10
    clusterDomain: cluster.local
  node:
    role: control-plane
    advertise_address: ''
    flags:
      node-ip: 0.0.0.0
    patches: {}
  addons:
    kube_proxy:
      managed_by: kubeadm
    coredns:
      managed_by: kubeadm
  orchestrate:
    transport: minion
    nodegroups:
      masters: k8s-masters
      workers: k8s-workers
    source_ip: master-1
    decommissioned: []
    health:
      retries: 30
      interval: 2
