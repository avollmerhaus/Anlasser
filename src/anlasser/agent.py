import asyncio
import logging
import signal
from pathlib import Path

from .sock_server import AnlasserSockServer
from .errors import AnlasserInvalidActionError
from .bhyve_controller import AnlasserBhyveController


class AnlasserShutdown(Exception):
    """Used to stop the TaskGroup"""

    pass


class AnlasserAgent:
    def __init__(self, vm_configs_dir, socket_path):
        self._vm_configs_dir = Path(vm_configs_dir)
        self._vms = dict()
        self._lock = asyncio.Lock()
        self._tg = None
        self._shutdown_event = asyncio.Event()
        self._sock_path = Path(socket_path)
        self._sock_server = AnlasserSockServer(self._sock_path, self._dispatcher)

    async def _dispatcher(self, payload):
        logging.debug("_dispatch lock taken")
        async with self._lock:
            try:
                # If action is unset, we'll get None.
                # That should land us in the default else block for
                # invalid actions down below.
                action = payload.get("action")
                body = payload.get("body", {})

                # Messages that make it into this part of the code are assumed to have passed schema verification.
                # See ANLASSER_REQUEST_SCHEMA from messages.py
                if action == "list_vms":
                    logging.info("Dispatch action: list_vms")
                    vm_list = [name for name in sorted(self._vms.keys())]
                    return {"vm_list": vm_list}

                if action == "set_vm_state":
                    logging.info("Dispatch action: set_vm_state")
                    vm_name = body["vm_name"]
                    target_state = body["state"]
                    return await self.set_vm_state(vm_name, target_state)

                if action == "get_vm_state":
                    vm_name = body["vm_name"]
                    logging.info(f"Dispatch action: get_vm_state {vm_name}")
                    state = "up" if vm_name in self._vms.keys() else "down"
                    return {"vm_state": state}

                logging.warning(f"Dispatch action: Invalid action: '{action}'")
                raise AnlasserInvalidActionError(f"Invalid action '{action}'")

            except asyncio.CancelledError:
                logging.debug("Stopping dispatch")
                # Is there something else we need to do here?
                raise

    async def set_vm_state(self, vm_name, target_state):
        logging.info(f"set_vm_state: {vm_name} -> {target_state}")
        if target_state == "down":
            if vm_name not in self._vms:
                return True
            task = self._vms[vm_name]["task"]
            task.cancel()
            await task
            return True
        if target_state == "up":
            if vm_name in self._vms:
                return True
            vm = AnlasserBhyveController(vm_name)
            vm.load_config(self._vm_config_path(vm_name))
            task = self._tg.create_task(vm.run())
            self._vms[vm_name] = {"task": task, "controller": vm}
            task.add_done_callback(lambda _task: self._vms.pop(vm_name, None))
            return True

        raise AnlasserInvalidActionError("target_state must be up or down")

    def _on_signal(self, sig_name):
        logging.info(f"Got signal {sig_name}, triggering shutdown procedure")
        self._shutdown_event.set()

    async def _await_shutdown_event(self):
        # Why not simply raise Shutdown directly from the signal handler?
        # Because the exception needs to be raised from within the TaskGroup,
        # and the signal handler function can't be part of that.
        await self._shutdown_event.wait()
        raise AnlasserShutdown()

    async def main(self):
        rc = 255

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGQUIT):
            # loop.add_signal has a different signature than the normal,
            # non-async add-handler function.
            loop.add_signal_handler(sig, self._on_signal, sig.name)

        try:
            async with asyncio.TaskGroup() as tg:
                # If we want a global shutdown ceiling, wrap this TaskGroup in asyncio.wait_for(...)
                # and cancel outstanding tasks on timeout.
                # Without that, a shutdown can hang if a task ignores cancellation or blocks on a lock.
                # We place responsibility for being cancellation-friendly on the tasks scheduled onto this TG.
                self._tg = tg
                tg.create_task(self._await_shutdown_event())
                tg.create_task(self._sock_server.serve())
        except* AnlasserShutdown:
            # What are the conditions that constitute an unclean shutdown?
            # Should we make sure all VMs have terminated gracefully?
            rc = 0
        return rc

    def _vm_config_path(self, vm_name):
        return self._vm_configs_dir / f"{vm_name}.ini"
