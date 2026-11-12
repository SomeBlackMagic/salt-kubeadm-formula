{#- Manage kubelet's formula-owned systemd unit and environment. #}
{%- from 'kubeadm/map.jinja' import kubeadm with context %}
{%- set rendered_flags = [] %}
{%- for flag, value in kubeadm.node.flags | dictsort %}
  {%- if value is sameas true %}
    {%- do rendered_flags.append('--' ~ flag) %}
  {%- elif value is sameas false %}
    {%- do rendered_flags.append('--' ~ flag ~ '=false') %}
  {%- else %}
    {%- do rendered_flags.append('--' ~ flag ~ '=' ~ value) %}
  {%- endif %}
{%- endfor %}
{%- set flags_string = rendered_flags | join(' ') %}

kubelet_unit:
  file.managed:
    - name: /etc/systemd/system/kubelet.service
    - source: salt://kubeadm/files/kubelet.service
    - template: jinja
    - mode: '0644'
    - context:
        bin_dir: {{ kubeadm.paths.bin_dir | tojson }}

kubelet_dropin:
  file.managed:
    - name: /etc/systemd/system/kubelet.service.d/10-kubeadm.conf
    - source: salt://kubeadm/files/10-kubeadm.conf
    - template: jinja
    - makedirs: True
    - mode: '0644'
    - context:
        bin_dir: {{ kubeadm.paths.bin_dir | tojson }}

kubelet_defaults:
  file.managed:
    - name: /etc/default/kubelet
    - makedirs: True
    - mode: '0644'
    - contents: |
        KUBELET_EXTRA_ARGS={{ flags_string | tojson }}

kubelet_daemon_reload:
  module.run:
    - name: service.systemctl_reload
    - onchanges:
      - file: kubelet_unit
      - file: kubelet_dropin

kubelet_service:
  service.enabled:
    - name: kubelet
    - require:
      - file: kubelet_unit
      - file: kubelet_dropin
      - module: kubelet_daemon_reload
