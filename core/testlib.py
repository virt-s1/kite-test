import argparse
import errno
import os
import shutil
import socket
import sys
import traceback
import re
import tempfile
import time
import unittest

import machine as machinelib


TEST_DIR = os.path.normpath(os.path.dirname(os.path.realpath(os.path.join(__file__, ".."))))

os.environ["PATH"] = "{0}:{1}".format(os.environ.get("PATH"), TEST_DIR)

__all__ = (
    # Test definitions
    'test_main',
    'arg_parser',
    'MachineCase',
    'timeout',
    'Error',

    'sit',
    'wait',
    'opts',
    'TEST_DIR',
)

# Command line options
opts = argparse.Namespace()
opts.sit = False
opts.trace = False
opts.attachments = None
opts.address = None
opts.user = None
opts.port = None


def attach(filename):
    if not opts.attachments:
        return
    dest = os.path.join(opts.attachments, os.path.basename(filename))
    if os.path.exists(filename) and not os.path.exists(dest):
        shutil.move(filename, dest)


class MachineCase(unittest.TestCase):
    machine = None
    runner = None
    journal_start = None

    def label(self):
        (unused, sep, label) = self.id().partition(".")
        return label.replace(".", "-")

    def checkSuccess(self):
        # errors is a list of (method, exception) calls (usually multiple
        # per method); None exception means success
        return not any(e[1] for e in self._outcome.errors)

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        # automatically cleaned up for a test, but you have to create it yourself
        self.vm_tmpdir = "/var/lib/kite_test"

        # create a machine
        self.machine = machinelib.Machine(
            user=opts.user,
            address=opts.address,
            ssh_port=opts.port,
            identity_file=opts.identity_file,
            verbose=opts.verbosity)
        # check machine reachable and ssh workable
        # abort/fail the test if one of them does not work
        self.machine.wait_boot()

        self.journal_start = self.machine.journal_cursor()
        # helps with mapping journal output to particular tests
        name = "%s.%s" % (self.__class__.__name__, self._testMethodName)
        self.machine.execute("logger -p user.info 'KITETEST: start %s'" % name)
        self.addCleanup(self.machine.execute, "logger -p user.info 'KITETEST: end %s'" % name)

        # core dumps get copied per-test, don't clobber subsequent tests with them
        self.addCleanup(self.machine.execute, "rm -rf /var/lib/systemd/coredump")

        # temporary directory in the VM
        self.addCleanup(
            self.machine.execute,
            "if [ -d {0} ]; then findmnt --list --noheadings --output "
            "TARGET | grep ^{0} | xargs -r umount && rm -r {0}; fi".format(self.vm_tmpdir))

    def tearDown(self):
        if self.checkSuccess() and self.machine.ssh_reachable:
            self.check_journal_messages()
        shutil.rmtree(self.tmpdir)

    allow_core_dumps = False

    # Whitelist of allowed journal messages during tests; these need to match the *entire* message
    allowed_messages = [
        # Reboots are ok
        "-- Reboot --",

        # ssh messages may be dropped when closing
        '10.*: dropping message while waiting for child to exit',

        # SELinux messages to ignore
        "(audit: )?type=1403 audit.*",
        "(audit: )?type=1404 audit.*",

        # our core dump retrieval is not entirely reliable
        "Failed to send coredump datagram:.*",

        # cursor
        "Failed to seek to cursor: Invalid argument"
    ]

    allowed_messages += os.environ.get("TEST_ALLOW_JOURNAL_MESSAGES", "").split(",")

    def allow_journal_messages(self, *patterns):
        """Don't fail if the journal contains a entry completely matching the given regexp"""
        for p in patterns:
            self.allowed_messages.append(p)

    def allow_hostkey_messages(self):
        self.allow_journal_messages('.*: .* host key for server is not known: .*',
                                    '.*: refusing to connect to unknown host: .*',
                                    '.*: failed to retrieve resource: hostkey-unknown')

    def allow_restart_journal_messages(self):
        self.allow_journal_messages(".*Connection reset by peer.*",
                                    "connection unexpectedly closed by peer",
                                    "peer did not close io when expected",
                                    "request timed out, closing",
                                    ".*: failed to retrieve resource: terminated",
                                    )

    def check_journal_messages(self, machine=None):
        """Check for unexpected journal entries."""
        machine = machine or self.machine
        # on main machine, only consider journal entries since test case start
        cursor = (machine == self.machine) and self.journal_start or None
        syslog_ids = ["kernel"]
        if not self.allow_core_dumps:
            syslog_ids += ["systemd-coredump"]
        messages = machine.journal_messages(syslog_ids, 5, cursor=cursor)
        if "TEST_AUDIT_NO_SELINUX" not in os.environ:
            messages += machine.audit_messages("14", cursor=cursor)  # 14xx is selinux

        all_found = True
        first = None
        for m in messages:
            # remove leading/trailing whitespace
            m = m.strip()
            # Ignore empty lines
            if not m:
                continue
            found = False

            # When coredump could not be generated, we cannot do much with info
            # about there being a coredump
            # Ignore this message and all subsequent core dumps
            # If there is more than just one line about coredump, it will fail
            # and show this messages
            if m == "Failed to generate stack trace: (null)":
                self.allowed_messages.append("Process .* of user .* dumped core.*")
                continue

            for p in self.allowed_messages:
                match = re.match(p, m)
                if match and match.group(0) == m:
                    found = True
                    break
            if not found:
                all_found = False
                if not first:
                    first = m
                print(m)
        if not all_found:
            self.copy_journal("FAIL")
            self.copy_cores("FAIL")
            raise Error("FAIL: Test completed, but found unexpected journal messages:\n" + first)

    def copy_journal(self, title, label=None):
        m = self.machine
        if m.ssh_reachable:
            log = "%s-%s-%s.log.gz" % (label or self.label(), m.label, title)
            with open(log, "w") as fp:
                m.execute("journalctl|gzip", stdout=fp)
                print("Journal extracted to %s" % (log))
                attach(log)

    def copy_cores(self, title, label=None):
        if self.allow_core_dumps:
            return
        m = self.machine
        if m.ssh_reachable:
            directory = "%s-%s-%s.core" % (label or self.label(), m.label, title)
            dest = os.path.abspath(directory)
            # overwrite core dumps from previous retries
            if os.path.exists(dest):
                shutil.rmtree(dest)
            m.download_dir("/var/lib/systemd/coredump", dest)
            try:
                os.rmdir(dest)
            except OSError as ex:
                if ex.errno == errno.ENOTEMPTY:
                    print("Core dumps downloaded to %s" % (dest))
                    attach(dest)

    def settle_cpu(self):
        '''Wait until CPU usage in the VM settles down
        Wait until the process with the highest CPU usage drops below 20%
        usage. Wait for up to a minute, then return. There is no error if the
        CPU stays busy, as usually a test then should just try to run anyway.
        '''
        for retry in range(20):
            # get the CPU percentage of the most busy process
            busy_proc = self.machine.execute(
                "ps --no-headers -eo pcpu,pid,args | sort -k 1 -n -r | head -n1")
            if float(busy_proc.split()[0]) < 20.0:
                break
            time.sleep(3)

    def sed_file(self, expr, path, apply_change_action=None):
        '''sed a file on test machine
        The file will be restored during cleanup.
        The optional apply_change_action will be run both after sedding
        and after restoring the file.
        '''
        m = self.machine
        m.execute("sed -i.kite_test '{0}' {1}".format(expr, path))
        if apply_change_action:
            m.execute(apply_change_action)

        if apply_change_action:
            self.addCleanup(m.execute, apply_change_action)
        self.addCleanup(m.execute, "mv {0}.kite_test {0}".format(path))

    def restore_dir(self, path, post_restore_action=None, reboot_safe=False):
        '''Backup/restore a directory for a test
        This takes care to not ever touch the original content on disk,
        but uses transient overlays.
        As this uses a bind mount, it does not work for files that get
        changed atomically (with mv); use restore_file() for these.
        The optional post_restore_action will run after restoring
        the original content.
        If the directory needs to survive reboot, `reboot_safe=True`
        needs to be specified; then this
        will just backup/restore the directory instead of bind-mounting,
        which is less robust.
        '''
        exists = self.machine.execute("if test -e %s; then echo yes; fi" % path).strip() != ""
        if not exists:
            self.addCleanup(self.machine.execute, "rm -rf {0}".format(path))
            return

        backup = os.path.join(self.vm_tmpdir, path.replace('/', '_'))
        self.machine.execute("mkdir -p %(vm_tmpdir)s && cp -a %(path)s/ %(backup)s/" % {
            "vm_tmpdir": self.vm_tmpdir, "path": path, "backup": backup})

        if not reboot_safe:
            self.machine.execute("mount -o bind %(backup)s %(path)s" % {
                "path": path, "backup": backup})

        if post_restore_action:
            self.addCleanup(self.machine.execute, post_restore_action)

        if reboot_safe:
            self.addCleanup(self.machine.execute, "rm -rf {0} && mv {1} {0}".format(path, backup))
        else:
            self.addCleanup(self.machine.execute, "umount -lf " + path)

    def restore_file(self, path, post_restore_action=None):
        '''Backup/restore a file for a test
        This is less robust than restore_dir(), but works for files that need
        to get changed atomically.
        If path does not currently exist, it will be removed again on cleanup.
        '''
        exists = self.machine.execute("if test -e %s; then echo yes; fi" % path).strip() != ""
        if exists:
            backup = os.path.join(self.vm_tmpdir, path.replace('/', '_'))
            self.machine.execute("mkdir -p %(vm_tmpdir)s && cp -a %(path)s %(backup)s" % {
                "vm_tmpdir": self.vm_tmpdir, "path": path, "backup": backup})
            if post_restore_action:
                self.addCleanup(self.machine.execute, post_restore_action)
            self.addCleanup(self.machine.execute,
                            "mv %(backup)s %(path)s" % {"path": path, "backup": backup})
        else:
            self.addCleanup(self.machine.execute, "rm -rf %s" % path)

    def write_file(self, path, content, append=False, owner=None, perm=None):
        '''Write a new file on primary machine
        This is safe for any tests, the file will be removed during cleanup.
        If @append is True, append to existing file instead of replacing it.
        @owner is the desired file owner as chown shell string (e.g. "admin:nogroup")
        @perm is the desired file permission as chmod shell string (e.g. "0600")
        '''
        m = self.machine
        m.write(path, content, append=append, owner=owner, perm=perm)

        self.addCleanup(m.execute, "rm -f {0}".format(path))


def timeout(seconds):
    """Change default test timeout of 600s, for long running tests
    Can be applied to an individual test method or the entire class. This only
    applies to test/verify/run-tests, not to calling check-* directly.
    """
    def wrapper(testEntity):
        testEntity.__timeout = seconds
        return testEntity

    return wrapper


class TapRunner:

    def __init__(self, verbosity=1):
        self.stream = unittest.runner._WritelnDecorator(sys.stderr)
        self.verbosity = verbosity

    def runOne(self, test):
        result = unittest.TestResult()
        print('# ----------------------------------------------------------------------')
        print('#', test)
        try:
            unittest.TestSuite([test]).run(result)
        except KeyboardInterrupt:
            result.addError(test, sys.exc_info())
            return result
        except Exception:
            result.addError(test, sys.exc_info())
            sys.stderr.write("Unexpected exception while running {0}\n".format(test))
            sys.stderr.write(traceback.format_exc())
            return result
        else:
            result.printErrors()

        if result.skipped:
            print("# Result {0} skipped: {1}".format(test, result.skipped[0][1]))
        elif result.wasSuccessful():
            print("# Result {0} succeeded".format(test))
        else:
            for error in result.errors:
                print(error[1])
            print("# Result {0} failed".format(test))
        return result

    def run(self, testable):
        tests = []

        # The things to test
        def collapse(test, tests):
            if isinstance(test, unittest.TestCase):
                tests.append(test)
            else:
                for t in test:
                    collapse(t, tests)
        collapse(testable, tests)
        test_count = len(tests)

        # For statistics
        start = time.time()
        failures = 0
        skips = []
        while tests:
            # The next test to test
            test = tests.pop(0)
            result = self.runOne(test)
            if not result.wasSuccessful():
                failures += 1
            skips += result.skipped

        # Report on the results
        duration = int(time.time() - start)
        hostname = socket.gethostname().split(".")[0]
        details = "[{0}s on {1}]".format(duration, hostname)

        # Return 77 if all tests were skipped
        if len(skips) == test_count:
            sys.stdout.write("# SKIP {0}\n".format(
                ", ".join(["{0} {1}".format(str(s[0]), s[1]) for s in skips])))
            return 77
        if failures:
            sys.stdout.write("# {0} TEST{1} FAILED {2}\n".format(
                failures, "S" if failures > 1 else "", details))
            return 1
        else:
            sys.stdout.write("# {0} TEST{1} PASSED {2}\n".format(
                test_count, "S" if test_count > 1 else "", details))
            return 0


def print_tests(tests):
    for test in tests:
        if isinstance(test, unittest.TestSuite):
            print_tests(test)
        elif isinstance(test, unittest.loader._FailedTest):
            name = test.id().replace("unittest.loader._FailedTest.", "")
            print("Error: '{0}' does not match a test".format(name), file=sys.stderr)
        else:
            print(test.id().replace("__main__.", ""))


def arg_parser(enable_sit=True):
    parser = argparse.ArgumentParser(description='Run kite test(s)')
    parser.add_argument('-v', '--verbose', dest="verbosity", action='store_const',
                        const=2, help='Verbose output')
    parser.add_argument('-t', "--trace", dest='trace', action='store_true',
                        help='Trace machine boot and commands')
    parser.add_argument('-q', '--quiet', dest='verbosity', action='store_const',
                        const=0, help='Quiet output')
    if enable_sit:
        parser.add_argument('-s', "--sit", dest='sit', action='store_true',
                            help="Sit and wait after test failure")
    parser.add_argument("-l", "--list", action="store_true",
                        help="Print the list of tests that would be executed")
    parser.add_argument('--user', dest="user", type=str, help="SSH login username")
    parser.add_argument('--address', dest="address", type=str, help="Test machine IP address")
    parser.add_argument('--port', dest="port", type=int, help="SSH port")
    parser.add_argument('--identity', dest="identity_file", type=str, help="SSH private key")
    parser.add_argument('tests', nargs='*')

    parser.set_defaults(verbosity=1, user="admin", port=22)
    return parser


def test_main():
    """
    Run all test cases, as indicated by arguments.
    If no arguments are given on the command line, all test cases are
    executed.  Otherwise only the given test cases are run.
    """

    global opts

    # Turn off python stdout buffering
    buf_arg = 0
    os.environ['PYTHONUNBUFFERED'] = '1'
    buf_arg = 1
    sys.stdout.flush()
    sys.stdout = os.fdopen(sys.stdout.fileno(), 'w', buf_arg)

    parser = arg_parser()
    options = parser.parse_args()

    # Sit should always imply verbose
    if options.sit:
        options.verbosity = 2

    # Have to copy into opts due to python globals across modules
    for (key, value) in vars(options).items():
        setattr(opts, key, value)

    opts.attachments = os.environ.get("TEST_ATTACHMENTS")
    if opts.attachments:
        os.makedirs(opts.attachments, exist_ok=True)

    import __main__
    if len(opts.tests) > 0:
        suite = unittest.TestLoader().loadTestsFromNames(opts.tests, module=__main__)
    else:
        suite = unittest.TestLoader().loadTestsFromModule(__main__)

    if options.list:
        print_tests(suite)
        return 0

    runner = TapRunner(verbosity=opts.verbosity)
    ret = runner.run(suite)
    sys.exit(ret)


class Error(Exception):
    def __init__(self, msg):
        self.msg = msg

    def __str__(self):
        return self.msg


def wait(func, msg=None, delay=1, tries=60):
    """
    Wait for FUNC to return something truthy, and return that.
    FUNC is called repeatedly until it returns a true value or until a
    timeout occurs.  In the latter case, a exception is raised that
    describes the situation.  The exception is either the last one
    thrown by FUNC, or includes MSG, or a default message.
    Arguments:
      func: The function to call.
      msg: A error message to use when the timeout occurs.  Defaults
        to a generic message.
      delay: How long to wait between calls to FUNC, in seconds.
        Defaults to 1.
      tries: How often to call FUNC.  Defaults to 60.
    Raises:
      Error: When a timeout occurs.
    """

    t = 0
    while t < tries:
        try:
            val = func()
            if val:
                return val
        except Exception:
            if t == tries - 1:
                raise
            else:
                pass
        t = t + 1
        time.sleep(delay)
    raise Error(msg or "Condition did not become true.")


def sit(machine):
    """
    Wait until the user confirms to continue.
    The current test case is suspended so that the user can inspect machine.
    """
    sys.stderr.write(machine.diagnose())
    print("Press RET to continue...")
    sys.stdin.readline()
