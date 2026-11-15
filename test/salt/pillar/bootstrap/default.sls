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
  cluster:
    clusterName: kitchen
    controlPlaneEndpoint: localhost:6443
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
    flags: {}
    patches: {}
