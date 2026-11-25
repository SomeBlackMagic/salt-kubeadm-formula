# -*- coding: utf-8 -*-
# vim: ft=yaml
---
base:
  '*':
    - default
  'master-1':
    - bootstrap.default
  '*_master':
    - bootstrap.default
  'buildkitsandbox_master':
    - bootstrap.default
  'G@os_family:Gentoo':
    - gentoo
  'os:*':
    - define_roles
...
