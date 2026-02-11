import asyncio
import json
from pathlib import Path

import anlasser.agent
from anlasser.agent import AnlasserController


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


class FakeBhyveDriver:
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


async def _run_agent_with_fake_driver(tmp_path, monkeypatch):
    monkeypatch.setattr(anlasser.agent, "AnlasserBhyveDriver", FakeBhyveDriver)
    socket_path = tmp_path / "anlasser.sock"
    controller = AnlasserController(vm_configs_dir=tmp_path, socket_path=socket_path)
    main_task = asyncio.create_task(controller.main())
    ready = await _wait_for_socket(socket_path)
    if not ready:
        main_task.cancel()
        await main_task
        raise RuntimeError("Timed out waiting for agent socket to appear")
    return controller, socket_path, main_task


async def _shutdown_agent(controller, main_task):
    controller._shutdown_event.set()
    await main_task


# Intention: verify dispatcher start/stop manages the unix socket lifecycle.
# Expected outcome: socket appears after start and is removed after shutdown.
def test_agent_socket_lifecycle(tmp_path, monkeypatch):
    async def scenario():
        controller, socket_path, main_task = await _run_agent_with_fake_driver(
            tmp_path, monkeypatch
        )
        assert socket_path.exists()
        await _shutdown_agent(controller, main_task)
        assert not socket_path.exists()

    asyncio.run(scenario())


# Intention: verify list_vms is routed correctly through the socket.
# Expected outcome: returns empty list when no VMs are tracked.
def test_agent_list_vms_request(tmp_path, monkeypatch):
    async def scenario():
        controller, socket_path, main_task = await _run_agent_with_fake_driver(
            tmp_path, monkeypatch
        )
        try:
            response = await _send_agent_request(socket_path, {"action": "list_vms"})
            assert response == {"success": True, "result": {"vm_list": []}}
        finally:
            await _shutdown_agent(controller, main_task)

    asyncio.run(scenario())


# Intention: verify vm start/stop requests are dispatched and tracked correctly.
# Expected outcome: start reports "up"; stop cancels the driver task and reports "down".
def test_agent_vm_start_stop_requests(tmp_path, monkeypatch):
    async def scenario():
        controller, socket_path, main_task = await _run_agent_with_fake_driver(
            tmp_path, monkeypatch
        )
        try:
            response = await _send_agent_request(
                socket_path,
                {"action": "set_vm_state", "vm_name": "vm1", "state": "up"},
            )
            assert response == {"success": True, "result": True}
            response = await _send_agent_request(
                socket_path, {"action": "get_vm_state", "vm_name": "vm1"}
            )
            assert response == {"success": True, "result": {"vm_state": "up"}}
            driver = controller._vms["vm1"]["driver"]

            response = await _send_agent_request(
                socket_path,
                {"action": "set_vm_state", "vm_name": "vm1", "state": "down"},
            )
            assert response == {"success": True, "result": True}
            await asyncio.wait_for(driver.stop_event.wait(), timeout=1.0)

            response = await _send_agent_request(
                socket_path, {"action": "get_vm_state", "vm_name": "vm1"}
            )
            assert response == {"success": True, "result": {"vm_state": "down"}}
        finally:
            await _shutdown_agent(controller, main_task)

    asyncio.run(scenario())
