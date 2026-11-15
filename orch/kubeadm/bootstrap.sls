{#- Create the first control-plane node. #}
{%- set transport = salt['pillar.get'](
      'kubeadm:orchestrate:transport', 'minion'
    ) %}
{%- set masters_nodegroup = salt['pillar.get'](
      'kubeadm:orchestrate:nodegroups:masters', ''
    ) %}
{%- set source_ip = salt['pillar.get'](
      'kubeadm:orchestrate:source_ip', None
    ) %}
{%- set endpoint = salt['pillar.get'](
      'kubeadm:cluster:controlPlaneEndpoint', ''
    ) %}
{%- set ignore_preflight_errors = salt['pillar.get'](
      'kubeadm:_ignore_preflight_errors', []
    ) %}
{%- set init_node = salt['saltutil.runner'](
      'kubeadm.pick_init_node',
      nodegroup=masters_nodegroup,
      source_ip=source_ip,
      transport=transport
    ) %}
{%- set cluster_exists = salt['saltutil.runner'](
      'kubeadm.cluster_exists',
      nodegroup=masters_nodegroup,
      transport=transport
    ) %}
{%- set skip_phases = [] %}
{%- if salt['pillar.get'](
      'kubeadm:addons:kube_proxy:managed_by', 'kubeadm'
    ) == 'external' %}
  {%- do skip_phases.append('addon/kube-proxy') %}
{%- endif %}
{%- if salt['pillar.get'](
      'kubeadm:addons:coredns:managed_by', 'kubeadm'
    ) == 'external' %}
  {%- do skip_phases.append('addon/coredns') %}
{%- endif %}

bootstrap_guard:
{%- if cluster_exists %}
  test.fail_without_changes:
    - name: Kubernetes cluster exists; bootstrap refused
{%- else %}
  test.succeed_without_changes:
    - name: Kubernetes cluster does not exist
{%- endif %}

endpoint_precheck:
  salt.runner:
    - name: kubeadm.endpoint_precheck
    - endpoint: {{ endpoint | tojson }}
    - require:
      - test: bootstrap_guard

containerd_on_init:
  salt.state:
    - tgt: {{ init_node | tojson }}
    - sls: containerd
    - test: {{ opts['test'] | tojson }}
{%- if transport == 'ssh' %}
    - ssh: True
{%- endif %}
    - require:
      - salt: endpoint_precheck

node_states_init:
  salt.state:
    - tgt: {{ init_node | tojson }}
    - sls: kubeadm
    - test: {{ opts['test'] | tojson }}
{%- if transport == 'ssh' %}
    - ssh: True
{%- endif %}
    - require:
      - salt: containerd_on_init

preflight:
  salt.state:
    - tgt: {{ init_node | tojson }}
    - sls: kubeadm.init_cluster
    - pillar:
        kubeadm:
          _phase: preflight
          _ignore_preflight_errors: {{ ignore_preflight_errors | tojson }}
    - test: {{ opts['test'] | tojson }}
{%- if transport == 'ssh' %}
    - ssh: True
{%- endif %}
    - require:
      - salt: node_states_init

init_cluster:
  salt.state:
    - tgt: {{ init_node | tojson }}
    - sls: kubeadm.init_cluster
    - pillar:
        kubeadm:
          _phase: init
          _skip_phases: {{ skip_phases | tojson }}
          _ignore_preflight_errors: {{ ignore_preflight_errors | tojson }}
    - test: {{ opts['test'] | tojson }}
{%- if transport == 'ssh' %}
    - ssh: True
{%- endif %}
    - require:
      - salt: preflight

init_health:
  salt.state:
    - tgt: {{ init_node | tojson }}
    - sls: kubeadm.init_cluster
    - pillar:
        kubeadm:
          _phase: health
    - test: {{ opts['test'] | tojson }}
{%- if transport == 'ssh' %}
    - ssh: True
{%- endif %}
    - require:
      - salt: init_cluster

bootstrap_done:
  salt.runner:
    - name: event.send
    - tag: kubeadm/bootstrap/done/{{ init_node }}
    - require:
      - salt: init_health
