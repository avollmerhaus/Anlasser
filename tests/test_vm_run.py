import pytest
import asyncio

from anlasser.vm import AnlasserVM
from anlasser.errors import AnlasserVMError

# Test philosophy:
# - We don't test internal implementation details.
# - We simulate bhyve behavior and assert that Anlasser drives the process lifecycle correctly.
# - We cover both happy paths and pathological paths (reboot loop, cancellation, shutdown timeout).


class FakeProc:
    def __init__(self, pid=1234, returncodes=None):
        self.pid = pid
        self._returncodes = returncodes or [0]
        self._wait_calls = 0
        self.returncode = None
        self.terminate_called = 0
        self.kill_called = 0
        self.wait_calls = 0

    def terminate(self):
        self.terminate_called += 1

    def kill(self):
        self.kill_called += 1

    async def wait(self):
        self.wait_calls += 1
        rc = self._returncodes[min(self._wait_calls, len(self._returncodes) - 1)]
        self._wait_calls += 1
        self.returncode = rc
        return rc


def test_run_restarts_on_reboot_then_stops(monkeypatch):
    procs = [
        FakeProc(pid=111, returncodes=[0]),
        FakeProc(pid=222, returncodes=[1]),
    ]

    async def fake_create_subprocess_exec(*args, **kwargs):
        return procs.pop(0)

    monkeypatch.setattr(
        asyncio, "create_subprocess_exec", fake_create_subprocess_exec
    )

    vm = AnlasserVM("testvm1", None)
    vm.bhyve_command = ["bhyve"]

    async def fake_network_setup():
        return True

    async def fake_network_teardown():
        return True

    vm._network_setup = fake_network_setup
    vm._network_teardown = fake_network_teardown

    asyncio.run(vm.run())

    assert procs == []


def test_run_cancel_calls_stop(monkeypatch):
    stop_called = False
    started_event = None
    proc = FakeProc(pid=333, returncodes=[0])

    async def fake_create_subprocess_exec(*args, **kwargs):
        return proc

    monkeypatch.setattr(
        asyncio, "create_subprocess_exec", fake_create_subprocess_exec
    )

    vm = AnlasserVM("testvm1", None)
    vm.bhyve_command = ["bhyve"]

    async def fake_network_setup():
        return True

    async def fake_network_teardown():
        return True

    async def fake_stop_bhyve(proc):
        nonlocal stop_called
        stop_called = True

    vm._network_setup = fake_network_setup
    vm._network_teardown = fake_network_teardown
    vm._stop_bhyve = fake_stop_bhyve

    async def run_with_start_signal():
        nonlocal started_event
        started_event = asyncio.Event()

        async def instrumented_wait():
            started_event.set()
            await asyncio.Event().wait()

        proc.wait = instrumented_wait
        task = asyncio.create_task(vm.run())
        await started_event.wait()
        task.cancel()
        await task

    asyncio.run(run_with_start_signal())

    assert stop_called is True


def test_stop_bhyve_kills_after_timeout():
    proc = FakeProc(pid=555, returncodes=[0])

    wait_calls = 0

    async def wait_with_timeout_then_exit():
        nonlocal wait_calls
        wait_calls += 1
        if wait_calls == 1:
            await asyncio.Event().wait()
        return 0

    proc.wait = wait_with_timeout_then_exit

    vm = AnlasserVM("testvm1", None)
    vm.shutdown_timeout = 0.01

    async def run_stop():
        await vm._stop_bhyve(proc)

    asyncio.run(run_stop())

    assert proc.terminate_called == 1
    assert proc.kill_called == 1
