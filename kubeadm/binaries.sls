{#- Install versioned Kubernetes binaries without switching existing nodes. #}
{%- from 'kubeadm/map.jinja' import kubeadm with context %}
{%- set binaries = ('kubeadm', 'kubelet', 'kubectl') %}
{%- set version_dir = kubeadm.paths.install_dir ~ '/' ~ kubeadm.version %}
{%- set binary_url = kubeadm.download.base_url ~ '/' ~ kubeadm.version
      ~ '/bin/linux/' ~ kubeadm.arch %}
{%- set existing_symlinks = [] %}
{%- for binary in binaries %}
  {%- if salt['file.is_link'](kubeadm.paths.bin_dir ~ '/' ~ binary) %}
    {%- do existing_symlinks.append(binary) %}
  {%- endif %}
{%- endfor %}
{%- set symlink_guard = 'true' if not existing_symlinks else 'false' %}

{%- for binary in binaries %}
kubeadm_binary_{{ binary }}:
  file.managed:
    - name: {{ version_dir }}/{{ binary }}
    - source: {{ binary_url }}/{{ binary }}
    - source_hash: {{ binary_url }}/{{ binary }}.sha256
    - makedirs: True
    - mode: '0755'

kubeadm_symlink_{{ binary }}:
  file.symlink:
    - name: {{ kubeadm.paths.bin_dir }}/{{ binary }}
    - target: {{ version_dir }}/{{ binary }}
    - makedirs: True
    - onlyif:
      - {{ symlink_guard | tojson }}
    - require:
      - file: kubeadm_binary_{{ binary }}
{%- endfor %}

{%- set installed_dirs = [] %}
{%- if salt['file.directory_exists'](kubeadm.paths.install_dir) %}
  {%- set installed_dirs = salt['file.find'](
        kubeadm.paths.install_dir,
        type='d',
        mindepth=1,
        maxdepth=1
      ) %}
{%- endif %}
{%- set stale_dirs = salt['kubeadm_util.stale_version_dirs'](
      installed_dirs,
      kubeadm.version,
      2
    ) %}
{%- for stale_dir in stale_dirs %}
kubeadm_old_version_{{ loop.index }}:
  file.absent:
    - name: {{ stale_dir }}
    - require:
      - file: kubeadm_binary_kubeadm
      - file: kubeadm_binary_kubelet
      - file: kubeadm_binary_kubectl
{%- endfor %}
