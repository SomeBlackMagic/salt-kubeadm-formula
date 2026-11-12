{#- Render kubeadm configuration documents and strategic merge patches. #}
{%- from 'kubeadm/map.jinja' import kubeadm with context %}
{%- set allowed_targets = (
      'kubeletconfiguration',
      'kube-apiserver',
      'kube-controller-manager',
      'kube-scheduler',
      'etcd'
    ) %}

kubeadm_config_dir:
  file.directory:
    - name: {{ kubeadm.paths.config_dir }}
    - makedirs: True
    - mode: '0700'

kubeadm_patches_dir:
  file.directory:
    - name: {{ kubeadm.paths.patches_dir }}
    - makedirs: True
    - mode: '0700'
    - clean: True

{%- for target, patch in kubeadm.node.patches | dictsort %}
  {%- if target not in allowed_targets %}
    {%- do raise('Unsupported kubeadm patch target: ' ~ target) %}
  {%- endif %}
  {%- set patch_content = salt['kubeadm_util.to_k8s'](
        patch,
        ['containers', 'initContainers', 'env', 'volumes',
         'volumeMounts', 'ports']
      ) %}
kubeadm_patch_{{ target | replace('-', '_') }}:
  file.managed:
    - name: {{ kubeadm.paths.patches_dir }}/{{ target }}+strategic.yaml
    - mode: '0600'
    - makedirs: True
    - contents: |
{{ patch_content | yaml(False) | indent(8, True) }}
    - require_in:
      - file: kubeadm_patches_dir
{%- endfor %}

kubeadm_init_config:
  file.managed:
    - name: {{ kubeadm.paths.config_dir }}/init.yaml
    - source: salt://kubeadm/files/init.yaml.jinja
    - template: jinja
    - mode: '0600'
    - require:
      - file: kubeadm_config_dir

kubeadm_cluster_config:
  file.managed:
    - name: {{ kubeadm.paths.config_dir }}/cluster.yaml
    - source: salt://kubeadm/files/cluster.yaml.jinja
    - template: jinja
    - mode: '0600'
    - require:
      - file: kubeadm_config_dir

kubeadm_kubelet_config:
  file.managed:
    - name: {{ kubeadm.paths.config_dir }}/kubelet.yaml
    - source: salt://kubeadm/files/kubelet.yaml.jinja
    - template: jinja
    - mode: '0600'
    - require:
      - file: kubeadm_config_dir

{%- if kubeadm.get('join', {}).get('token') %}
kubeadm_join_config:
  file.managed:
    - name: {{ kubeadm.paths.config_dir }}/join.yaml
    - source: salt://kubeadm/files/join.yaml.jinja
    - template: jinja
    - mode: '0600'
    - show_changes: False
    - require:
      - file: kubeadm_config_dir
{%- endif %}
