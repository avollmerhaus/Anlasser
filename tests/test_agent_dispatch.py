import asyncio
import json
import os
import signal
from pathlib import Path

import anlasser.agent
from anlasser.agent import AnlasserAgent


async def _wait_for_socket(socket_path, timeout=0.2):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if socket_path.exists():
            return True
        await asyncio.sleep(0.01)
    return False


async def _send_agent_request(socket_path, payload):
    reader, writer = await asyncio.open_unix_connection(str(socket_path))
    try:
        writer.write(json.dumps(payload).encode("utf-8") + b"\n")
        await writer.drain()
        response = await reader.readline()
        return json.loads(response.decode("utf-8"))
    finally:
        writer.close()
        await writer.wait_closed()


class FakeBhyveController:
    def __init__(self, name):
        self.name = name
        self.loaded_config_path = None
        self.stop_event = asyncio.Event()

    def load_config(self, config_path):
        self.loaded_config_path = Path(config_path)

    async def run(self):
        try:
            while True:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            self.stop_event.set()


async def _run_agent_with_fake_bhyve_controller(tmp_path, monkeypatch):
    monkeypatch.setattr(anlasser.agent, "AnlasserBhyveController", FakeBhyveController)
    socket_path = tmp_path / "anlasser.sock"
    agent = AnlasserAgent(vm_configs_dir=tmp_path, socket_path=socket_path)
    main_task = asyncio.create_task(agent.main())
    ready = await _wait_for_socket(socket_path)
    if not ready:
        main_task.cancel()
        await main_task
        raise RuntimeError("Timed out waiting for agent socket to appear")
    return agent, socket_path, main_task


async def _shutdown_agent(agent, main_task):
    agent._shutdown_event.set()
    await main_task


# Intention: verify dispatcher start/stop manages the unix socket lifecycle.
# Expected outcome: socket appears after start and is removed after shutdown.
def test_agent_socket_lifecycle(tmp_path, monkeypatch):
    async def scenario():
        agent, socket_path, main_task = await _run_agent_with_fake_bhyve_controller(
            tmp_path, monkeypatch
        )
        assert socket_path.exists()
        await _shutdown_agent(agent, main_task)
        assert not socket_path.exists()

    asyncio.run(scenario())


# Intention: verify list_vms is routed correctly through the socket.
# Expected outcome: returns empty list when no VMs are tracked.
def test_agent_list_vms_request(tmp_path, monkeypatch):
    async def scenario():
        agent, socket_path, main_task = await _run_agent_with_fake_bhyve_controller(
            tmp_path, monkeypatch
        )
        try:
            response = await _send_agent_request(
                socket_path, {"action": "list_vms", "body": {}}
            )
            assert response == {"status": 200, "body": {"response": {"vm_list": []}}}
        finally:
            await _shutdown_agent(agent, main_task)

    asyncio.run(scenario())


# Intention: verify list_vms returns started VMs.
# Expected outcome: after starting a VM, list_vms includes its name.
def test_agent_list_vms_with_running_vm(tmp_path, monkeypatch):
    async def scenario():
        agent, socket_path, main_task = await _run_agent_with_fake_bhyve_controller(
            tmp_path, monkeypatch
        )
        try:
            await _send_agent_request(
                socket_path,
                {"action": "set_vm_state", "body": {"vm_name": "vm1", "state": "up"}},
            )
            response = await _send_agent_request(
                socket_path, {"action": "list_vms", "body": {}}
            )
            assert response == {
                "status": 200,
                "body": {"response": {"vm_list": ["vm1"]}},
            }
        finally:
            await _shutdown_agent(agent, main_task)

    asyncio.run(scenario())


# Intention: verify get_vm_state for a VM that was never started.
# Expected outcome: returns "down".
def test_agent_get_vm_state_unknown_vm(tmp_path, monkeypatch):
    async def scenario():
        agent, socket_path, main_task = await _run_agent_with_fake_bhyve_controller(
            tmp_path, monkeypatch
        )
        try:
            response = await _send_agent_request(
                socket_path,
                {"action": "get_vm_state", "body": {"vm_name": "nonexistent"}},
            )
            assert response == {
                "status": 200,
                "body": {"response": {"vm_state": "down"}},
            }
        finally:
            await _shutdown_agent(agent, main_task)

    asyncio.run(scenario())


# Intention: verify SIGTERM triggers graceful shutdown of running VMs.
# Expected outcome: VM task receives cancellation when agent gets SIGTERM.
# Together with test_controller_cancel_calls_stop (which verifies that cancellation
# calls terminate on the bhyve proc), this covers the full SIGTERM→VM shutdown chain.
def test_agent_sigterm_cancels_running_vm(tmp_path, monkeypatch):
    async def scenario():
        agent, socket_path, main_task = await _run_agent_with_fake_bhyve_controller(
            tmp_path, monkeypatch
        )
        await _send_agent_request(
            socket_path,
            {"action": "set_vm_state", "body": {"vm_name": "vm1", "state": "up"}},
        )
        vm = agent._vms["vm1"]["controller"]

        os.kill(os.getpid(), signal.SIGTERM)
        await main_task

        assert vm.stop_event.is_set()

    asyncio.run(scenario())


# Intention: verify vm start/stop requests are dispatched and tracked correctly.
# Expected outcome: start reports "up"; stop cancels the VM task and reports "down".
def test_agent_vm_start_stop_requests(tmp_path, monkeypatch):
    async def scenario():
        agent, socket_path, main_task = await _run_agent_with_fake_bhyve_controller(
            tmp_path, monkeypatch
        )
        try:
            response = await _send_agent_request(
                socket_path,
                {"action": "set_vm_state", "body": {"vm_name": "vm1", "state": "up"}},
            )
            assert response == {"status": 200, "body": {"response": "ok"}}
            response = await _send_agent_request(
                socket_path, {"action": "get_vm_state", "body": {"vm_name": "vm1"}}
            )
            assert response == {"status": 200, "body": {"response": {"vm_state": "up"}}}
            vm = agent._vms["vm1"]["controller"]

            response = await _send_agent_request(
                socket_path,
                {"action": "set_vm_state", "body": {"vm_name": "vm1", "state": "down"}},
            )
            assert response == {"status": 200, "body": {"response": "ok"}}
            await asyncio.wait_for(vm.stop_event.wait(), timeout=1.0)

            response = await _send_agent_request(
                socket_path, {"action": "get_vm_state", "body": {"vm_name": "vm1"}}
            )
            assert response == {
                "status": 200,
                "body": {"response": {"vm_state": "down"}},
            }
        finally:
            await _shutdown_agent(agent, main_task)

    asyncio.run(scenario())
