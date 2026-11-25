{#- Bootstrap-only kubeadm phases selected by private orchestration pillar. #}
{%- from 'kubeadm/map.jinja' import kubeadm with context %}
{%- set phase = salt['pillar.get']('kubeadm:_phase', '') %}
{%- set ignore_errors = salt['pillar.get'](
      'kubeadm:_ignore_preflight_errors', []
    ) %}
{%- set skip_phases = salt['pillar.get']('kubeadm:_skip_phases', []) %}
{%- set rendered_config %}
{% include 'kubeadm/files/init.yaml.jinja' %}
{%- endset %}

{%- if phase == 'preflight' %}
kubeadm_preflight:
  kubeadm.preflight:
    - config: {{ kubeadm.paths.config_dir }}/init.yaml
    - pull_images: True
    - ignore_preflight_errors: {{ ignore_errors | tojson }}

{%- elif phase == 'init' %}
kubeadm_init:
  kubeadm.initialized:
    - config: {{ kubeadm.paths.config_dir }}/init.yaml
    - patches: {{ kubeadm.paths.patches_dir }}
    - patches_in_config: True
    - upload_certs: True
    - skip_phases: {{ skip_phases | tojson }}
    - ignore_preflight_errors: {{ ignore_errors | tojson }}
    - rendered_config: |
{{ rendered_config | trim | indent(8, True) }}

{%- elif phase == 'health' %}
kubeadm_health:
  kubeadm.healthy:
    - checks:
      - readyz
      - etcd
    - retries: {{ kubeadm.orchestrate.health.retries }}
    - interval: {{ kubeadm.orchestrate.health.interval }}

{%- else %}
kubeadm_unknown_phase:
  test.fail_without_changes:
    - comment: "Unknown kubeadm bootstrap phase: {{ phase }}"
{%- endif %}
