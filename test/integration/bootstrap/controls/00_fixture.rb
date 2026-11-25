# frozen_string_literal: true

control 'bootstrap-fixture' do
  impact 1.0
  title 'Prepared environment supports both Salt transports'

  %w[salt-master salt-minion ssh haproxy].each do |service_name|
    describe service(service_name) do
      it { should be_running }
    end
  end

  describe command('salt --out=json --timeout=30 master-1 test.ping') do
    its('exit_status') { should eq 0 }
    its('stdout') { should match(/true/) }
  end

  describe command('timeout 60 salt-ssh -i --out=json master-1 test.ping') do
    its('exit_status') { should eq 0 }
    its('stdout') { should match(/true/) }
  end
end
