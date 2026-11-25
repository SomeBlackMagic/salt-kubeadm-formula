{#- Create the first control-plane node over minion or salt-ssh. #}
{%- set transport = salt['pillar.get'](
      'kubeadm:orchestrate:transport', 'minion'
    ) %}
{%- if transport not in ('minion', 'ssh') %}
  {%- do raise('Unknown kubeadm transport: ' ~ transport) %}
{%- endif %}
{%- set masters_nodegroup = salt['pillar.get'](
      'kubeadm:orchestrate:nodegroups:masters', ''
    ) %}
{%- set source_ip = salt['pillar.get'](
      'kubeadm:orchestrate:source_ip', None
    ) %}
{%- set endpoint = salt['pillar.get'](
      'kubeadm:cluster:controlPlaneEndpoint', ''
    ) %}
{%- set cluster_exists = salt['saltutil.runner'](
      'kubeadm.cluster_exists',
      nodegroup=masters_nodegroup,
      transport=transport,
      endpoint=endpoint,
      source_ip=source_ip
    ) %}
{%- if cluster_exists is not sameas true
      and cluster_exists is not sameas false %}
  {%- do raise('Unable to determine whether the cluster exists: '
               ~ cluster_exists) %}
{%- endif %}
{%- set init_node = salt['saltutil.runner'](
      'kubeadm.pick_init_node',
      nodegroup=masters_nodegroup,
      source_ip=source_ip,
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

{%- if cluster_exists %}
bootstrap_guard:
  test.fail_without_changes:
    - name: >-
        Kubernetes cluster exists; use the control-plane join workflow
{%- else %}
bootstrap_guard:
  test.nop:
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
    - test: {{ opts.get('test', False) | tojson }}
{%- if transport == 'ssh' %}
    - ssh: True
{%- endif %}
    - require:
      - salt: endpoint_precheck

node_states_init:
  salt.state:
    - tgt: {{ init_node | tojson }}
    - sls: kubeadm
    - test: {{ opts.get('test', False) | tojson }}
{%- if transport == 'ssh' %}
    - ssh: True
{%- endif %}
    - require:
      - salt: containerd_on_init

preflight:
  salt.state:
    - tgt: {{ init_node | tojson }}
    - sls: kubeadm.init_cluster
    - test: {{ opts.get('test', False) | tojson }}
{%- if transport == 'ssh' %}
    - ssh: True
{%- endif %}
    - pillar:
        kubeadm:
          _phase: preflight
    - require:
      - salt: node_states_init

init_cluster:
  salt.state:
    - tgt: {{ init_node | tojson }}
    - sls: kubeadm.init_cluster
    - test: {{ opts.get('test', False) | tojson }}
{%- if transport == 'ssh' %}
    - ssh: True
{%- endif %}
    - pillar:
        kubeadm:
          _phase: init
          _skip_phases: {{ skip_phases | tojson }}
    - require:
      - salt: preflight

init_health:
  salt.state:
    - tgt: {{ init_node | tojson }}
    - sls: kubeadm.init_cluster
    - test: {{ opts.get('test', False) | tojson }}
{%- if transport == 'ssh' %}
    - ssh: True
{%- endif %}
    - pillar:
        kubeadm:
          _phase: health
    - require:
      - salt: init_cluster

{%- if not opts.get('test', False) %}
bootstrap_done:
  salt.runner:
    - name: event.send
    - tag: {{ ('kubeadm/bootstrap/done/' ~ init_node) | tojson }}
    - require:
      - salt: init_health
{%- endif %}
