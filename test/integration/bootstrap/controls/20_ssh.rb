# frozen_string_literal: true

# TODO: orchestration tests temporarily disabled — Kitchen/InSpec is not suited
# for long-running salt-run commands.  These will move to a pytest-based harness
# or lifecycle hooks.

# control 'bootstrap-over-ssh' do
#   impact 1.0
#   title 'The prepared fixture can bootstrap the same node over salt-ssh'
#
#   describe command(
#     'timeout 120 salt-ssh -i master-1 state.apply kubeadm.reset ' \
#     "pillar='{" \
#     '"kubeadm":{"confirm_reset":true}' \
#     "}' --out=json"
#   ) do
#     its('exit_status') { should eq 0 }
#   end
#
#   describe command(
#     'timeout 120 salt-run state.orchestrate orch.kubeadm.bootstrap ' \
#     'saltenv=base pillarenv=base test=True ' \
#     "pillar='{" \
#     '"kubeadm":{"orchestrate":{"transport":"ssh"}}' \
#     "}'"
#   ) do
#     its('exit_status') { should eq 0 }
#     its('stdout') { should match(/InitConfiguration/) }
#   end
#
#   describe file('/etc/kubernetes/admin.conf') do
#     it { should_not exist }
#   end
#
#   describe command(
#     'timeout 300 salt-run state.orchestrate orch.kubeadm.bootstrap ' \
#     'saltenv=base pillarenv=base ' \
#     "pillar='{" \
#     '"kubeadm":{"orchestrate":{"transport":"ssh"}}' \
#     "}'"
#   ) do
#     its('exit_status') { should eq 0 }
#     its('stdout') { should match(/bootstrap_done/) }
#   end
#
#   describe file('/etc/kubernetes/admin.conf') do
#     it { should be_file }
#   end
#
#   describe command('timeout 30 curl --fail --insecure https://127.0.0.1:6443/readyz') do
#     its('exit_status') { should eq 0 }
#   end
#
#   describe command('timeout 30 curl --fail http://127.0.0.1:2381/health') do
#     its('exit_status') { should eq 0 }
#   end
#
#   describe command(
#     'timeout 120 salt-run state.orchestrate orch.kubeadm.bootstrap ' \
#     'saltenv=base pillarenv=base ' \
#     "pillar='{" \
#     '"kubeadm":{"orchestrate":{"transport":"ssh"}}' \
#     "}'"
#   ) do
#     its('exit_status') { should_not eq 0 }
#     its('stdout') { should match(/cluster exists/i) }
#   end
# end
