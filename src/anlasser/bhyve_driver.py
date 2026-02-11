import asyncio
import logging
from pathlib import Path

from .bhyve_driver_config import load_bhyve_driver_config
from .bhyve_driver_networking import (
    tap_operation,
    wait_for_tap_device_creation,
)

from .errors import AnlasserBhyveDriverError

class AnlasserBhyveDriver:
    """Bhyve VM driver.

    """

    def __init__(self, name):
        # VM config (public)
        self.name = name
        self.memory_mb = None
        self.cpu_sockets = None
        self.cpu_cores = None
        self.cpu_threads = None
        self.storage_path = None
        self.uefi_vars_storage_path = None
        self.mac = None
        self.tapdev = None
        self.bridge = None
        self.vnc_port = None
        self.vnc_kbd_layout = None
        self.vnc_wait_connect = None
        self.iso_path = None
        self.bhyve_command = None
        self.shutdown_timeout = 90

        # Runtime state
        self._bootstrap_done = False


    def load_config(self, config_path):
        load_bhyve_driver_config(self, config_path)

    async def _network_setup(self):
        if self._bootstrap_done:
            return True

        try:
            await wait_for_tap_device_creation(self.tapdev)
        except TimeoutError as exc:
            logging.error(f"VM {self.name}: {exc}")
            return False
        if await tap_operation("add", self.tapdev, self.bridge):
            # FIXME: this assumes a single tap device
            self._bootstrap_done = True
            return True
        return False

    async def _network_teardown(self):
        if not self._bootstrap_done:
            logging.info(f"VM {self.name}: No network teardown necessary")
            return True

        if await tap_operation("destroy", self.tapdev):
            self._bootstrap_done = False
            return True
        return False

    async def _stop_bhyve(self, proc):
        if proc is None or proc.returncode is not None:
            # FIXME: this if condition is an inelegant mess.
            # We could simplify, but maybe we should remove it altogether?
            # Shouldn't the controller have made sure that we've been removed from the list
            # of running VMs if we have no proc or it's done?
            # There could be a miniscule race condition if _stop is called while we're initializing
            # a VM object. Maybe we should guard against that with a simple mechanism here.
            # We also need to have a test that makes sure the callback is effective, I guess.
            logging.warning(
                f"VM {self.name}: Not running (proc={'none' if proc is None else 'done'}, rc={None if proc is None else proc.returncode})"
            )
            return

        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), self.shutdown_timeout)
        except asyncio.TimeoutError:
            logging.error(f"VM {self.name}: Shutdown timeout expired, killing VM bhyve")
            proc.kill()
            # kill should prevent proc.wait() not returning,
            # but maybe we want to make sure we're not stuck here in the future.
            try:
                await asyncio.wait_for(proc.wait(), 2.0)
            except asyncio.TimeoutError:
                # FIXME: What does it mean to hit this codepath?
                # Leaking a subprocess?
                # But kill calls can't be blocked, could we even hit this condition?
                logging.error(
                    f"VM {self.name}: Kill timeout expired, giving up on bhyve wait"
                )

    async def run(self):
        proc = None
        try:
            if self.bhyve_command is None:
                raise AnlasserBhyveDriverError(
                    "run() invoked w/o config. You have to load a config using load_config() first."
                )
            if Path(f"/dev/vmm/{self.name}").exists():
                raise AnlasserBhyveDriverError(
                    f"VM {self.name}: Refusing to start, device node /dev/vmm/{self.name} already exists. "
                    "This indicates a stale or still-running VM context. If you are sure the VM is not running, "
                    f"clean it up with: bhyvectl --destroy --vm={self.name}"
                )
            # Let's initialize into the reboot state. We break out when bhyve sets a different exit code.
            rc = 0
            while rc == 0:
                if self._bootstrap_done:
                    logging.info(f"VM {self.name}: exit 0, reboot")
                else:
                    logging.info(f"Starting VM {self.name}")
                proc = await asyncio.create_subprocess_exec(
                    *self.bhyve_command,
                    start_new_session=True,
                )
                logging.info(f"VM {self.name}: Subprocess started, pid={proc.pid}")
                if not self._bootstrap_done:
                    await self._network_setup()
                rc = await proc.wait()
                logging.info(f"VM {self.name}: Subprocess exited, rc={rc}")
                # Prevent runaway load should we somehow get into an infinite loop,
                # with bhyve constantly exiting with status 0.
                await asyncio.sleep(0.5)
            # Bhyve exit codes (man bhyve):
            # 0 - reboot
            # 1 - power off
            # 2 - halted
            # 3 - triple fault
            # 4 - exited due to an error
            if rc == 0:
                logging.info(
                    "Bhyve exit status 0 (ordinary reboot), starting new process"
                )
            elif rc == 1:
                logging.info(
                    "Bhyve exit status 1 (ordinary shutdown), not restarting"
                )
            else:
                logging.info(
                    f"Bhyve exit status {rc}, not restarting"
                )
        except asyncio.CancelledError:
            logging.info(
                f"VM {self.name} pid={proc.pid} cancelled, shutting down"
            )
            await self._stop_bhyve(proc)
            return
        finally:
            await self._network_teardown()
