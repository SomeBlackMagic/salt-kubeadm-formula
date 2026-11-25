# frozen_string_literal: true

# TODO: orchestration and remote-state tests temporarily disabled —
# Kitchen/InSpec is not suited for long-running salt-run / salt <minion>
# commands.  These will move to a pytest-based harness or lifecycle hooks.

# control 'bootstrap-over-minion' do
#   impact 1.0
#   title 'Bootstrap is safe and creates a healthy control plane over minion'
#
#   describe command(
#     'timeout 120 salt --timeout=60 master-1 state.apply kubeadm.reset ' \
#     "pillar='{" \
#     '"kubeadm":{"confirm_reset":true}' \
#     "}' --out=json"
#   ) do
#     its('exit_status') { should eq 0 }
#   end
#
#   describe command('timeout 60 salt --timeout=30 master-1 state.apply kubeadm.reset --out=json') do
#     its('exit_status') { should_not eq 0 }
#     its('stdout') { should match(/confirm_reset/) }
#   end
# end
