# -*- coding: utf-8 -*-
# vim: ft=sls

{#- Get the `tplroot` from `tpldir` #}
{%- set tplroot = tpldir.split('/')[0] %}
{%- from tplroot ~ "/map.jinja" import mapdata as kubeadm with context %}

kubeadm-service-clean-service-dead:
  service.dead:
    - name: {{ kubeadm.service.name }}
    - enable: False
