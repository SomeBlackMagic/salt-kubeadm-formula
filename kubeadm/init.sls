{#- Phase 0 only validates and exposes the merged kubeadm map. #}
{%- from 'kubeadm/map.jinja' import kubeadm with context %}

kubeadm-foundations-loaded:
  test.nop:
    - name: >-
        kubeadm foundations loaded for role
        {{ kubeadm.node.role or 'unspecified' }}
