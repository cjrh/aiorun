import os
import signal
import subprocess as sp
import sys
import time

import pytest

if sys.platform == "win32":
    pytest.skip("Windows doesn't use POSIX signals", allow_module_level=True)


def test_sig():
    """Basic SIGTERM"""
    proc = sp.Popen(
        "python tests/fake_main.py".split(), stdout=sp.PIPE, stderr=sp.STDOUT
    )
    time.sleep(0.5)
    os.kill(proc.pid, signal.SIGTERM)
    proc.wait(timeout=5)
    stdout = proc.stdout.read().decode()
    assert proc.returncode == 0

    expected = [
        "Entering run()",
        "Entering shutdown phase",
        "Cancelling pending tasks",
        "Closing the loop",
        "Bye!",
    ]

    for item in expected:
        assert item in stdout


def test_sig_dec():
    """Basic SIGTERM"""
    proc = sp.Popen(
        "python tests/fake_main_dec.py".split(), stdout=sp.PIPE, stderr=sp.STDOUT
    )
    time.sleep(0.5)
    os.kill(proc.pid, signal.SIGTERM)
    proc.wait(timeout=5)
    stdout = proc.stdout.read().decode()
    assert proc.returncode == 0

    expected = [
        "Entering run()",
        "Entering shutdown phase",
        "Cancelling pending tasks",
        "Closing the loop",
        "Bye!",
    ]

    print(stdout)
    for item in expected:
        assert item in stdout


def test_sig_dec_call():
    """Basic SIGTERM"""
    proc = sp.Popen(
        "python tests/fake_main_dec_call.py".split(), stdout=sp.PIPE, stderr=sp.STDOUT
    )
    time.sleep(0.5)
    os.kill(proc.pid, signal.SIGTERM)
    proc.wait(timeout=5)
    stdout = proc.stdout.read().decode()
    assert proc.returncode == 0

    expected = [
        "Entering run()",
        "Entering shutdown phase",
        "Cancelling pending tasks",
        "Closing the loop",
        "Bye!",
    ]

    print(stdout)
    for item in expected:
        assert item in stdout
