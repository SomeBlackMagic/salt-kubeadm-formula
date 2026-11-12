# frozen_string_literal: true

control 'kubeadm-binaries' do
  title 'Install verified Kubernetes binaries and initial symlinks'

  %w[kubeadm kubelet kubectl].each do |binary|
    describe file("/opt/kubernetes/v1.25.16/#{binary}") do
      it { should be_file }
      it { should be_executable }
    end

    describe file("/usr/local/bin/#{binary}") do
      it { should be_symlink }
      its('link_path') { should eq "/opt/kubernetes/v1.25.16/#{binary}" }
    end
  end
end

control 'kubelet-systemd' do
  title 'Configure and enable kubelet without starting it'

  describe file('/etc/systemd/system/kubelet.service') do
    it { should be_file }
    its('content') { should include 'ExecStart=/usr/local/bin/kubelet' }
  end

  describe file('/etc/systemd/system/kubelet.service.d/10-kubeadm.conf') do
    it { should be_file }
    its('content') { should include 'EnvironmentFile=-/etc/default/kubelet' }
    its('content') { should include '--config=/var/lib/kubelet/config.yaml' }
  end

  describe file('/etc/default/kubelet') do
    its('content') { should include '--node-ip=10.0.0.12' }
  end

  describe service('kubelet') do
    it { should be_enabled }
    it { should_not be_running }
  end
end

control 'kubeadm-config' do
  title 'Render kubeadm documents and strategic merge patches'

  %w[init.yaml cluster.yaml kubelet.yaml].each do |config|
    describe file("/etc/kubernetes/kubeadm/#{config}") do
      it { should be_file }
      its('mode') { should cmp '0600' }
    end
  end

  describe file('/etc/kubernetes/kubeadm/join.yaml') do
    it { should_not exist }
  end

  describe file('/etc/kubernetes/patches/kubeletconfiguration+strategic.yaml') do
    it { should be_file }
    its('mode') { should cmp '0600' }
    its('content') { should include 'maxPods: 200' }
  end
end
