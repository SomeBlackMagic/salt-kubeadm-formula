# Systemd-capable image for the bootstrap Kitchen suite. The external
# containerd formula is installed by kitchen-salt as a suite dependency.
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

# The production node-prerequisite formula owns these OS tools. The minimal
# Kitchen base image supplies them explicitly; containerd/crictl still come
# exclusively from salt-containerd-formula during orchestration.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      conntrack iproute2 iptables socat \
 && rm -rf /var/lib/apt/lists/*
