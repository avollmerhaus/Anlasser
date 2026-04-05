import asyncio
from pathlib import Path

import pytest

from anlasser.bhyve_controller import AnlasserBhyveController
from anlasser.errors import AnlasserBhyveControllerError

# Test philosophy:
# - We don't test internal implementation details.
# - We simulate bhyve behavior and assert that Anlasser drives the process lifecycle correctly.
# - We cover both happy paths and pathological paths (reboot loop, cancellation, shutdown timeout).


# Lists of instances of this object are used by the test harness to
# simulate the subprocess objects created by `asyncio.create_subprocess_exec`.
# Also used to record calls made to the object, so we can later `assert` them.
class FakeProc:
    def __init__(
        self,
        pid=1234,
        fixed_rc=0,
        simulate_hang=False,
        bhyve_proc_halted_event=None,
    ):
        self.pid = pid

        # Match asyncio.subprocess API: returncode is None until wait() completes.
        # So we'll save it and set it later.
        self._fixed_rc = fixed_rc

        # When True, terminate() does NOT release wait(); only kill() does.
        # This models a hung shutdown so _stop_bhyve has to time out and kill.
        self._simulate_hang = simulate_hang

        # This event allows a test to react when
        # our fake proc has started simulating a real wait.
        self.wait_started_event = asyncio.Event()

        # wait() blocks on this event.
        # Tests can supply a pre-set event from the outside,
        # in order to avoid having to concern themselves with triggering the real shutdown logic.
        self.wait_release_event = bhyve_proc_halted_event or asyncio.Event()

        self.returncode = None
        self.terminate_called = 0
        self.kill_called = 0
        self.wait_calls = 0

    def terminate(self):
        self.terminate_called += 1
        if not self._simulate_hang:
            self.wait_release_event.set()

    def kill(self):
        self.kill_called += 1
        self.wait_release_event.set()

    async def wait(self):
        self.wait_calls += 1
        self.wait_started_event.set()
        # Simulate a running process by waiting until event is set.
        await self.wait_release_event.wait()
        self.returncode = self._fixed_rc
        return self.returncode


# RunHarness is used to instrument the bhyve controller.
# The `monkeypatch` object gets supplied by the pytest code
# when initializing the fixture.
# We use it to fake the actual subprocess creation.
# Methods of the vm controller object can be overridden directly,
# w/o using monkeypatch (because we have direct access to the object).
# List `procs` should be filled with `FakeProc` objects from the outside
# in accordance to the needs of the test.
class RunHarness:
    def __init__(
        self,
        monkeypatch,
        vm_name="testvm1",
    ):
        self.network_setup_calls = 0
        self.network_teardown_calls = 0
        self.destroy_vmm_calls = 0
        self.spawn_calls = 0
        self.procs = []

        self.vm = AnlasserBhyveController(vm_name)

        # The object's run function expects `bhyve_command` to be set.
        self.vm.bhyve_command = ["bhyve"]

        async def fake_create_subprocess_exec(*args, **kwargs):
            self.spawn_calls += 1
            return self.procs.pop(0)

        monkeypatch.setattr(
            asyncio, "create_subprocess_exec", fake_create_subprocess_exec
        )

        # Let's override the network and vmm cleanup methods,
        # they'll be tested separately.
        self.vm._network_setup = self.fake_network_setup
        self.vm._network_teardown = self.fake_network_teardown
        self.vm._destroy_vmm_device_node = self.fake_destroy_vmm_device_node

    async def fake_network_setup(self):
        self.network_setup_calls += 1
        self.vm._bootstrap_done = True
        return True

    async def fake_network_teardown(self):
        self.network_teardown_calls += 1
        self.vm._bootstrap_done = False
        return True

    async def fake_destroy_vmm_device_node(self):
        self.destroy_vmm_calls += 1

    # We need an async function for testing controller cancellation,
    # since we need to release program control in order for the vm controller to run.
    async def simulate_controller_cancel_helper(self, proc):
        # Schedule run of the VM controller object
        vm_run_task = asyncio.create_task(self.vm.run())
        # Now block until the vm controller starts awaiting the proc object using its `wait()` method.
        # That confirms the vm controller has begun waiting for proc to finish, like during normal operation.
        await proc.wait_started_event.wait()
        # We now simulate a stop request, by canceling the vm controller task.
        # That triggers `_stop_bhyve()`, which calls `terminate()` on the proc object.
        vm_run_task.cancel()
        # Relinquish control again, so the vm controller can finish.
        await vm_run_task


@pytest.fixture
def run_harness(monkeypatch):
    # Fixture needs to be an instance of the class
    return RunHarness(monkeypatch)


# Intention: verify run() respawns Bhyve on exit 0 and stops on exit 1.
# Expected outcome: exactly 2 spawns (initial start + one reboot).
# Any more or fewer spawns means run() mishandled exit codes and/or restarted too much or too little.
def test_controller_handles_bhyve_reboot(run_harness):
    bhyve_proc_halted_event = asyncio.Event()
    # Pre-set bhyve quit event: Circumvent simulated vm controller shutdown logic.
    bhyve_proc_halted_event.set()
    run_harness.procs.extend(
        [
            # First proc, simulates reboot
            FakeProc(fixed_rc=0, bhyve_proc_halted_event=bhyve_proc_halted_event),
            # Second proc, simulates bhyve VM poweroff
            FakeProc(fixed_rc=1, bhyve_proc_halted_event=bhyve_proc_halted_event),
        ]
    )

    asyncio.run(run_harness.vm.run())

    assert run_harness.spawn_calls == 2


# Intention: verify setup/teardown behavior around a clean shutdown.
# Expected outcome: network setup and teardown are each called once.
def test_controller_activates_setup_teardown(run_harness):
    bhyve_proc_halted_event = asyncio.Event()
    bhyve_proc_halted_event.set()
    # Pre-set bhyve quit event: Circumvent simulated vm controller shutdown logic.
    run_harness.procs.append(
        FakeProc(fixed_rc=1, bhyve_proc_halted_event=bhyve_proc_halted_event)
    )

    asyncio.run(run_harness.vm.run())

    assert run_harness.network_setup_calls == 1
    assert run_harness.network_teardown_calls == 1


# Intention: refuse to start if a stale device node already exists.
# Expected outcome: run() raises AnlasserBhyveControllerError before spawning bhyve.
def test_controller_quits_on_stale_device_node(run_harness, monkeypatch):
    vm_dev_path = Path(f"/dev/vmm/{run_harness.vm.name}")

    def fake_exists(path):
        # `Path` implements `__eq__` by comparing the path string.
        # We return True only for the VM's device node to simulate a stale /dev/vmm entry.
        # All other paths return False so we don't miss the check due to a path mismatch.
        # If we returned True for every path, we'd mask a bug where run() checks the wrong path
        # and would incorrectly continue to spawn bhyve despite a stale device node.
        return path == vm_dev_path

    monkeypatch.setattr(Path, "exists", fake_exists)

    # Make sure we raise AnlasserBhyveControllerError
    with pytest.raises(AnlasserBhyveControllerError):
        asyncio.run(run_harness.vm.run())

    # Make sure we didn't try to launch bhyve anyway
    assert run_harness.spawn_calls == 0


# Intention: Test ordinary shutdown of running VM controller task.
# Expected outcome: stop logic is invoked, calling terminate on the simulated bhyve proc object
def test_controller_cancel_calls_stop(run_harness):
    proc = FakeProc()
    run_harness.procs.append(proc)

    # Fail fast if something goes wrong, default timeout is high
    run_harness.vm.shutdown_timeout = 0.1

    asyncio.run(run_harness.simulate_controller_cancel_helper(proc))

    # Controller should have called `terminate()`, but not `kill()`
    assert proc.terminate_called == 1
    assert proc.kill_called == 0


# Intention: simulate a hung shutdown via task cancellation.
# Expected outcome: controller calls `terminate()`, then `kill()`
def test_controller_kills_hung_bhyve(run_harness):
    proc = FakeProc(simulate_hang=True)
    run_harness.procs.append(proc)

    # Fail fast if something goes wrong, default timeout is high
    run_harness.vm.shutdown_timeout = 0.1

    asyncio.run(run_harness.simulate_controller_cancel_helper(proc))

    # Assert normal shutdown was attempted
    assert proc.terminate_called == 1
    # Assert kill was called after that was not successful
    assert proc.kill_called == 1
