.. _readme:

kubeadm-formula
===============

|img_travis| |img_sr| |img_pc|

.. |img_travis| image:: https://travis-ci.com/saltstack-formulas/kubeadm-formula.svg?branch=master
   :alt: Travis CI Build Status
   :scale: 100%
   :target: https://travis-ci.com/saltstack-formulas/kubeadm-formula
.. |img_sr| image:: https://img.shields.io/badge/%20%20%F0%9F%93%A6%F0%9F%9A%80-semantic--release-e10079.svg
   :alt: Semantic Release
   :scale: 100%
   :target: https://github.com/semantic-release/semantic-release
.. |img_pc| image:: https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit&logoColor=white
   :alt: pre-commit
   :scale: 100%
   :target: https://github.com/pre-commit/pre-commit

A Salt formula for preparing Kubernetes nodes managed with ``kubeadm``.
The node-install phase downloads checksum-verified, versioned binaries,
installs the kubelet systemd unit without starting it, and renders kubeadm
configuration and strategic-merge patches from pillar data.

.. contents:: **Table of Contents**
   :depth: 1

General notes
-------------

See the full `SaltStack Formulas installation and usage instructions
<https://docs.saltproject.io/en/latest/topics/development/conventions/formulas.html>`_.

If you are interested in writing or contributing to formulas, please pay attention to the `Writing Formula Section
<https://docs.saltproject.io/en/latest/topics/development/conventions/formulas.html#writing-formulas>`_.

If you want to use this formula, please pay attention to the ``FORMULA`` file and/or ``git tag``,
which contains the currently released version. This formula is versioned according to `Semantic Versioning <http://semver.org/>`_.

See `Formula Versioning Section <https://docs.saltproject.io/en/latest/topics/development/conventions/formulas.html#versioning>`_ for more details.

If you need (non-default) configuration, please refer to:

- `how to configure the formula with map.jinja <map.jinja.rst>`_
- the ``pillar.example`` file
- the `Special notes`_ section

Contributing to this repo
-------------------------

Commit messages
^^^^^^^^^^^^^^^

**Commit message formatting is significant!!**

Please see `How to contribute <https://github.com/saltstack-formulas/.github/blob/master/CONTRIBUTING.rst>`_ for more details.

pre-commit
^^^^^^^^^^

`pre-commit <https://pre-commit.com/>`_ is configured for this formula, which you may optionally use to ease the steps involved in submitting your changes.
First install  the ``pre-commit`` package manager using the appropriate `method <https://pre-commit.com/#installation>`_, then run ``bin/install-hooks`` and
now ``pre-commit`` will run automatically on each ``git commit``. ::

  $ bin/install-hooks
  pre-commit installed at .git/hooks/pre-commit
  pre-commit installed at .git/hooks/commit-msg

Special notes
-------------

The main state deliberately enables kubelet but does not start it. Cluster
bootstrap and node joins are handled by later orchestration phases.

Existing binary symlinks are not switched by ``kubeadm.binaries``. A new
version can therefore be staged safely; activation belongs to the upgrade
workflow. The two most recent installed version directories are retained.

``join.yaml`` is rendered only when a runtime join token exists. Its contents
are hidden from Salt change output.

Available states
----------------

.. contents::
   :local:

``kubeadm``
^^^^^^^^^^^

Meta-state that includes ``kubeadm.binaries``, ``kubeadm.kubelet``, and
``kubeadm.config``.

``kubeadm.binaries``
^^^^^^^^^^^^^^^^^^^^

Downloads ``kubeadm``, ``kubelet``, and ``kubectl`` into a versioned
directory and verifies each binary with the upstream SHA-256 file. Initial
symlinks are created atomically; existing symlinks are left unchanged.

``kubeadm.kubelet``
^^^^^^^^^^^^^^^^^^^

Manages the kubelet systemd unit, drop-in, and ``/etc/default/kubelet``.
The service is enabled but is never started by this state.

``kubeadm.config``
^^^^^^^^^^^^^^^^^^

Renders version-appropriate kubeadm API documents under
``/etc/kubernetes/kubeadm`` and manages the clean patch directory under
``/etc/kubernetes/patches``. See ``pillar.example`` for the complete data
shape and merge layers.

Testing
-------

Linux testing is done with ``kitchen-salt``.

Requirements
^^^^^^^^^^^^

* Ruby
* Docker

.. code-block:: bash

   $ gem install bundler
   $ bundle install
   $ bin/kitchen test [platform]

Where ``[platform]`` is a platform name defined in ``kitchen.yml``, for
example ``debian-13-3008-5``.

``bin/kitchen converge``
^^^^^^^^^^^^^^^^^^^^^^^^

Creates the docker instance and runs the ``kubeadm`` main state, ready for testing.

``bin/kitchen verify``
^^^^^^^^^^^^^^^^^^^^^^

Runs the ``inspec`` tests on the actual instance.

``bin/kitchen destroy``
^^^^^^^^^^^^^^^^^^^^^^^

Removes the docker instance.

``bin/kitchen test``
^^^^^^^^^^^^^^^^^^^^

Runs all of the stages above in one go: i.e. ``destroy`` + ``converge`` + ``verify`` + ``destroy``.

``bin/kitchen login``
^^^^^^^^^^^^^^^^^^^^^

Gives you SSH access to the instance for manual testing.
