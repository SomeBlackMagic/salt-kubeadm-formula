# frozen_string_literal: true

control 'bootstrap-over-minion' do
  impact 1.0
  title 'Bootstrap is safe and creates a healthy control plane over minion'

  describe command(
    'salt master-1 state.apply kubeadm.reset ' \
    "pillar='{" \
    '"kubeadm":{"confirm_reset":true}' \
    "}' --out=json"
  ) do
    its('exit_status') { should eq 0 }
  end

  describe command('salt master-1 state.apply kubeadm.reset --out=json') do
    its('exit_status') { should_not eq 0 }
    its('stdout') { should match(/confirm_reset/) }
  end

  describe command(
    'salt-run state.orchestrate orch.kubeadm.bootstrap ' \
    'saltenv=base pillarenv=base test=True'
  ) do
    its('exit_status') { should eq 0 }
    its('stdout') { should match(/InitConfiguration/) }
  end

  describe file('/etc/kubernetes/admin.conf') do
    it { should_not exist }
  end

  describe command('systemctl stop haproxy') do
    its('exit_status') { should eq 0 }
  end

  describe command(
    'salt-run state.orchestrate orch.kubeadm.bootstrap ' \
    'saltenv=base pillarenv=base'
  ) do
    its('exit_status') { should_not eq 0 }
    its('stdout') { should match(/endpoint_precheck/) }
  end

  describe command('systemctl start haproxy') do
    its('exit_status') { should eq 0 }
  end

  describe command('systemctl start --no-block kubeadm-event-listener') do
    its('exit_status') { should eq 0 }
  end

  describe command(
    'timeout 300 salt-run state.orchestrate orch.kubeadm.bootstrap ' \
    'saltenv=base pillarenv=base'
  ) do
    its('exit_status') { should eq 0 }
    its('stdout') { should match(/bootstrap_done/) }
  end

  describe file('/tmp/kubeadm-bootstrap-event.log') do
    its('content') { should match(%r{kubeadm/bootstrap/done/master-1}) }
  end

  describe file('/etc/kubernetes/admin.conf') do
    it { should be_file }
    its('mode') { should cmp '0600' }
  end

  %w[kube-apiserver.yaml etcd.yaml kube-controller-manager.yaml
     kube-scheduler.yaml].each do |manifest|
    describe file("/etc/kubernetes/manifests/#{manifest}") do
      it { should be_file }
    end
  end

  describe command('curl --fail --insecure https://127.0.0.1:6443/readyz') do
    its('exit_status') { should eq 0 }
  end

  describe command('curl --fail http://127.0.0.1:2381/health') do
    its('exit_status') { should eq 0 }
  end

  describe command(
    'salt-run state.orchestrate orch.kubeadm.bootstrap ' \
    'saltenv=base pillarenv=base'
  ) do
    its('exit_status') { should_not eq 0 }
    its('stdout') { should match(/cluster exists/i) }
  end
end
