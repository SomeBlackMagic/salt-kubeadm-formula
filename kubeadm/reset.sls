{#- Explicitly confirmed cleanup for failed bootstrap or node decommission. #}
{%- if salt['pillar.get']('kubeadm:confirm_reset', False) %}
kubeadm_reset:
  cmd.run:
    - name: kubeadm reset -f
    - onlyif: >-
        test -f /etc/kubernetes/admin.conf -o
        -f /etc/kubernetes/kubelet.conf

kubeadm_cleanup:
  file.absent:
    - names:
      - /etc/kubernetes
      - /var/lib/etcd
      - /etc/cni/net.d
    - require:
      - cmd: kubeadm_reset

{%- else %}
kubeadm_reset_refused:
  test.fail_without_changes:
    - comment: "kubeadm reset requires kubeadm:confirm_reset: true in pillar"
{%- endif %}
