# Pre-baked image for the bootstrap Kitchen suite.
# Installs containerd via the same Salt state used in the formula tests,
# so that Kitchen converge does not have to repeat it on every run.
FROM ghcr.io/someblackmagic/salt-docker-images/kitchen-debian-13:3008.5

# Prevent getty from spawning on the host's TTYs in privileged mode.
# https://github.com/ansible/molecule/issues/1104
# https://github.com/moby/moby/issues/4040#issuecomment-339022455
RUN rm -rf /lib/systemd/system/systemd*udev* \
           /lib/systemd/system/getty.target \
 && cp /bin/true /sbin/agetty \
 && systemctl mask tmp.mount

# kitchen-docker skips user creation when a custom Dockerfile is provided.
# The kitchen user is required by the provisioner (salt_solo) and docker transport.
RUN useradd -d /home/kitchen -m -s /bin/bash -p '*' kitchen \
 && mkdir -p /etc/sudoers.d \
 && echo "kitchen ALL=(ALL) NOPASSWD: ALL" >> /etc/sudoers.d/kitchen \
 && echo "Defaults !requiretty" >> /etc/sudoers.d/kitchen

COPY test/fixtures/formulas/containerd /srv/salt/test/fixtures/formulas/containerd
RUN ln -s /srv/salt/test/fixtures/formulas/containerd /srv/salt/containerd

RUN salt-call --local --file-root=/srv/salt \
      state.sls_id bootstrap_containerd_packages containerd \
      --log-level=info \
 && salt-call --local --file-root=/srv/salt \
      state.sls_id bootstrap_containerd_config containerd \
      --log-level=info \
 && systemctl enable containerd \
 && rm -rf /srv/salt /var/cache/salt
