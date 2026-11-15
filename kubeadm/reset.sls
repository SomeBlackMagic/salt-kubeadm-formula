{#- Destructive reset requires explicit pillar confirmation. #}
{%- if salt['pillar.get']('kubeadm:confirm_reset', False) %}
kubeadm_reset:
  cmd.run:
    - name: kubeadm reset -f
    - onlyif: test -f /etc/kubernetes/admin.conf -o -f /etc/kubernetes/kubelet.conf

kubeadm_cleanup:
  file.absent:
    - names:
      - /etc/kubernetes
      - /var/lib/etcd
      - /etc/cni/net.d
    - require:
      - cmd: kubeadm_reset

kubeadm_iptables_cleanup:
  module.run:
    - name: iptables.flush
    - require:
      - cmd: kubeadm_reset
{%- else %}
reset_refused:
  test.fail_without_changes:
    - comment: "kubeadm reset requires confirm_reset: true in pillar"
{%- endif %}
