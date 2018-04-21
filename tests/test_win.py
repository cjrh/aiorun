import sys
import os
import time
import signal
import subprocess as sp
import pytest


if sys.platform != 'win32':
    pytest.skip("These tests are for Windows compatibility.",
                allow_module_level=True)


def test_sig():
    """Basic SIGTERM"""
    proc = sp.Popen(
        'python tests/fake_main.py'.split(),
        stdout=sp.PIPE,
        stderr=sp.STDOUT,
        creationflags=sp.CREATE_NEW_PROCESS_GROUP
    )
    time.sleep(0.5)
    # proc.send_signal(signal.CTRL_BREAK_EVENT)
    # os.kill(proc.pid, signal.CTRL_C_EVENT)
    os.kill(proc.pid, signal.CTRL_BREAK_EVENT)
    print('Send signal')
    proc.wait(timeout=5)
    stdout = proc.stdout.read().decode()
    print(stdout)
    assert proc.returncode == 0

    expected = [
        'Entering run()',
        'Received signal: SIGBREAK',
        'Entering shutdown phase',
        'Cancelling pending tasks',
        'Closing the loop',
        'Bye!',
    ]

    assert all(phrase in stdout for phrase in expected)
