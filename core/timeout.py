import signal


class Timeout:
    """ Add a timeout to an operation
        Specify machine to ensure that a machine's ssh operations are canceled
        when the timer expires.
    """
    def __init__(self, seconds=1, error_message='Timeout', machine=None):
        if signal.getsignal(signal.SIGALRM) != signal.SIG_DFL:
            # there is already a different Timeout active
            self.seconds = None
            return

        self.seconds = seconds
        self.error_message = error_message
        self.machine = machine

    def handle_timeout(self, signum, frame):
        if self.machine:
            if self.machine.ssh_process:
                self.machine.ssh_process.terminate()
            self.machine.disconnect()

        raise RuntimeError(self.error_message)

    def __enter__(self):
        if self.seconds:
            signal.signal(signal.SIGALRM, self.handle_timeout)
            signal.alarm(self.seconds)

    def __exit__(self, type, value, traceback):
        if self.seconds:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, signal.SIG_DFL)
