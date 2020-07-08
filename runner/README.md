# Deploy github runner on OpenStack

Ansible playbook to create an OpenStack instance, install required packages, and register instance to github.

## Required environment variable

    VAULT_PASSWORD    Decrypt openstack cloud config file

## Required playbook argument

    runner_token    Get from Actions-Add new runner

## Running command

    $ VAULT_PASSWORD=<vault password> ansible-playbook -v -i inventory -e runner_token=<token> deploy-runner.yml
