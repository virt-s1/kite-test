# kite-test

![linting](https://github.com/virt-s1/kite-test/workflows/linting/badge.svg?branch=master)
![RHEL 8.x on AWS EC2](https://github.com/virt-s1/kite-test/workflows/RHEL%208.x%20on%20AWS%20EC2/badge.svg)
![RHEL 8.x on ESXi 7.0](https://github.com/virt-s1/kite-test/workflows/RHEL%208.x%20on%20ESXi%207.0/badge.svg)
![RHEL 8.x on OpenStack](https://github.com/virt-s1/kite-test/workflows/RHEL%208.x%20on%20OpenStack/badge.svg)

`kite-test` includes a simple test framework and test cases together to make running and writing test as easy as possible.

Test framework is based on unittest with some enhancement to work perfect with Linux application/kernel test on cloud, like AWS EC2, VMWare ESXi, and OpenStack.

Test code is located in `test` folder and framework can be found in `core` folder.

## How to run test?

Run a single test suite:

    test/check-ltp -vst --user=<wheel group user> --address=<VM/instance IP address> --port=<SSH port>

Parameters:

    -h, --help                  Show this help message and exit
    -v, --verbose               Verbose output
    -t, --trace                 Trace machine boot and commands
    -q, --quiet                 Quiet output
    -s, --sit                   Sit and wait after test failure
    -l, --list                  Print the list of tests that would be executed
    --user USER                 SSH login username
    --address ADDRESS           Test machine IP address
    --port PORT                 SSH port
    --identity IDENTITY_FILE    SSH private key

Try to run test with wheel group user,  not `root`.

## How to write test case?

The test case totally follows Python unittest. The only difference is use sub-class `testlib.MachineCase` instead of `unittest.TestCase`. The [sample](./test/sample) is a good sample for writing test case. Here're some tips of writing test case:

* Please name your test file with `check-*` format without extension. That'll be easy for us to run test case.
* Try to write test case for all cloud platforms, and make test case as common as possible.
* Test case here is not grouped by cloud platform, but grouped by feature.
* Try to use `addCleanup` to clean up all you setup after each test because reboot on cloud platform is not a good choice.
* Please do not clean up your setup in `tearDown` and use `addCleanup` instead. And write `addCleanup` just after your setup, that makes your test easy to read and clean.
* `kite-test` test framework provides some helper functions which are really helpful when you write test case.
  * `execute()`: run any shell command and return output back.
  * `upload()`: upload one and more files to test machine.
  * `download()`: download one file from test machine to local.
  * `download_dir()`: download a folder from test machine to local.
  * `write()`: write content to a file in test machine.
  * `sed_file()`: use regRex to sed a file in test machine.
  * `restore_file()`: restore a file in test machine after test.
  * `restore_dir()`: restore a folder in test machine after test
* `kite-test` test framework will check kernel journalctl log after each test, any logs found will make test fail.
* `kite-test` test framework also checks core dump file and download it back if it exists after each test.
* Test case code follows `flake8` linting rule. Please lint your code before send PR.

## Test for test case

Test case sending in PR will trigger Github Action CI. All test cases in PR will be run on cloud platforms, include AWS EC2 ,VMWare ESXi, and OpenStack. Passing tests on cloud platforms is a must-have condition of PR merge.
