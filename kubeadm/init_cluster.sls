{#- Bootstrap phases, invoked only from orch.kubeadm.bootstrap. #}
{%- from 'kubeadm/map.jinja' import kubeadm with context %}
{%- set phase = salt['pillar.get']('kubeadm:_phase', '') %}
{%- set skip_phases = salt['pillar.get']('kubeadm:_skip_phases', []) %}
{%- set ignore_preflight_errors = salt['pillar.get'](
      'kubeadm:_ignore_preflight_errors', []
    ) %}
{%- if phase == 'init' %}
  {%- set rendered_init_config %}
    {%- include 'kubeadm/files/init.yaml.jinja' %}
  {%- endset %}
{%- endif %}

{%- if phase == 'preflight' %}
kubeadm_preflight:
  kubeadm.preflight:
    - config: {{ kubeadm.paths.config_dir }}/init.yaml
    - pull_images: True
    - ignore_preflight_errors: {{ ignore_preflight_errors | tojson }}

{%- elif phase == 'init' %}
kubeadm_init:
  kubeadm.initialized:
    - config: {{ kubeadm.paths.config_dir }}/init.yaml
    - patches: {{ kubeadm.paths.patches_dir }}
    - upload_certs: True
    - skip_phases: {{ skip_phases | tojson }}
    - ignore_preflight_errors: {{ ignore_preflight_errors | tojson }}
    - rendered_config: |
        {{ rendered_init_config | trim | indent(8) }}

{%- elif phase == 'health' %}
kubeadm_health:
  kubeadm.healthy:
    - checks:
      - readyz
      - etcd
    - retries: {{ kubeadm.orchestrate.health.retries }}
    - interval: {{ kubeadm.orchestrate.health.interval }}

{%- else %}
kubeadm_bootstrap_phase_invalid:
  test.fail_without_changes:
    - comment: "Unknown kubeadm bootstrap phase: {{ phase }}"
{%- endif %}
