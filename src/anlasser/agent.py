import asyncio
import logging
import signal
from pathlib import Path

from .sock_server import AnlasserSockServer
from .errors import AnlasserInvalidActionError, AnlasserError
from .vm import AnlasserVM

# the plan
# - create the workq from here
# - create the taskgroup from here
# - anlasservmcontroller should be imported
# - anlassersockserver should be imported
# - supply these with the workq and await them
# - cancel on AnlasserShutdown exception. maybe raise it from the AgentCLI?
#   or maybe we need something like an at exit function and cancel from the CLI?
# - events from the vmcontroller or sockserver will be put on the workq
# - the dispatcher that deals with them lives here!

class AnlasserShutdown(Exception):
    """ Used to stop the TaskGroup """    
    pass

class AnlasserController:
    def __init__(self, vm_configs_dir, socket_path):
        self._vm_configs_dir = Path(vm_configs_dir)
        self._vms = dict()
        self.workq = asyncio.Queue()
        self._shutdown_event = asyncio.Event()
        self._sock_path = Path(socket_path)
        self._sock_server = AnlasserSockServer(self._sock_path, self.workq)

    async def _dispatcher(self, tg):
        try:
            while True:
                payload, response_future = await self.workq.get()
                # If action is unset, we'll get None.
                # That should land us in the default else block for
                # invalid actions down below.
                action = payload.get("action")
                
                # EXTERNAL REQUESTS, we assume `response_future` to be an instance of `asyncio.Future` from here on!
                # We use set_result / set_exception to signal success or failure.
                # Messages that make it into this part of the code are assumed to have passed schema verification.
                # See ANLASSER_REQUEST_SCHEMA from messages.py
                try:
                    if action == "list_vms":
                        logging.info("Dispatch: list_vms")
                        vm_list = [name for name in sorted(self._vms.keys())]
                        action_result = {"vm_list": vm_list}
                    
                    elif action == "set_vm_state":
                        logging.info(f"Dispatch: set_vm_state")
                        # FIXME: inline this? Or make it a method of this object?
                        vm_name = payload["vm_name"]
                        target_state = payload["state"]
                        action_result = await self.set_vm_state(vm_name, target_state, tg)

                    elif action == "get_vm_state":
                        vm_name = payload["vm_name"]
                        logging.info(f"Dispatch: get_vm_state {vm_name}")
                        state = "up" if vm_name in self._vms.keys() else "down"
                        action_result = {"vm_state": state}

                    else:
                        logging.warning(f"Dispatch: Invalid action: '{action}'")
                        raise AnlasserInvalidActionError(f"Invalid action '{action}'")

                    response_future.set_result(action_result)
                    continue


                except AnlasserError as exc:
                    if not response_future.done():
                        response_future.set_exception(exc)

            
        except asyncio.CancelledError:
            logging.debug("Stopping dispatch")
            # Is there something we need to do here?
            # Maybe cancel fut if it's not None?
            raise

    async def set_vm_state(self, vm_name, target_state, tg):
        logging.info(f"set_vm_state: {vm_name} -> {target_state}")
        # need to use task group from here in case we have to start a VM!
        # we also need to check if the VM is already up!
        # if target state is down, but the vm isn't in the vm list, raise a spe
        if target_state == "down":
            if vm_name not in self._vms:
                return True
            task = self._vms[vm_name]
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            return True
        if target_state == "up":
            if vm_name in self._vms:
                return True
            vm = AnlasserVM(vm_name, self._on_vm_exit)
            task = tg.create_task(vm.run())
            self._vms[vm_name] = task
            return True  # Real start logic goes here, should raise AnlasserInvalidVMConfigError if config not found or corrupt!

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
                tg.create_task(self._await_shutdown_event())
                # Does the dispatcher make sense as an extra task?
                # Couldn't we pass the taskgroup directly to the client msg handler?
                # What about handling multiple clients? Could locking fix that? Do we even need locking?
                # There is only one task actually active with async!
                # We also need a way for VMs that go powerdown to remove themselves from self._vms!
                tg.create_task(self._dispatcher(tg))
                tg.create_task(self._sock_server.serve())
        except* AnlasserShutdown:
            # What about unclean shutdown?
            # Do we need to make sure all VMs have terminated gracefully?
            rc = 0
        finally:
            await self.cleanup()
        return rc

    async def cleanup(self):
        logging.info("should we use a job queue, maybe that would get cleaned up here?")

    def _vm_config_path(self, vm_name):
        return self._vm_configs_dir / f"{vm_name}.ini"

    def _on_vm_exit(self, vm_name):
        self._vms.pop(vm_name, None)
