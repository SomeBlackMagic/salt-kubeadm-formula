# Test double for the external formula: provide a real container runtime.
---
bootstrap_containerd_packages:
  pkg.installed:
    - pkgs:
        - containerd
        - containernetworking-plugins

bootstrap_containerd_config:
  file.managed:
    - name: /etc/containerd/config.toml
    - source: salt://test/fixtures/formulas/containerd/files/config.toml
    - makedirs: true
    - require:
        - pkg: bootstrap_containerd_packages

bootstrap_containerd_service:
  service.running:
    - name: containerd
    - enable: true
    - watch:
        - file: bootstrap_containerd_config
