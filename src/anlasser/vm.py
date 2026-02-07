import asyncio
import logging
from pathlib import Path

from .errors import AnlasserVMError
from .vm_config import load_vm_config
from .vm_networking import (
    tap_operation,
    wait_for_tap_device_creation,
)

class AnlasserVM:

    def __init__(self, name, on_exit):
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
        self._on_exit = on_exit

    def load_config(self, config_path):
        load_vm_config(self, config_path)

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
                logging.error(
                    f"VM {self.name}: Kill timeout expired, giving up on bhyve wait"
                )

    async def _bhyvectl_destroy(self):
        # As long as the "--vm=(name)" parameter is present and the name has a device node at /dev/vmm/(name),
        # bhyvectl will gladly accept whatever bullshit you throw at it and __only__ raise a syntax error
        # if the bullshit has two dashes in front of it. So pay good attention when modifying the command!
        # Forgetting some dashes in front of a command may leave you scratching your head.
        command = ["bhyvectl", "--destroy", f"--vm={self.name}"]
        logging.info(f"Running command: {command}")
        proc = await asyncio.create_subprocess_exec(
            *command,
            start_new_session=True,
        )
        rc = await proc.wait()
        if rc > 0:
            logging.error(f"Error running bhyvectl: {rc}")

    async def run(self):
        proc = None
        try:
            if self.bhyve_command is None:
                raise AnlasserVMError(
                    "run() invoked w/o config. You have to load a config using load_config() first."
                )
            # exit status 0: normaler reboot
            # exit status 1: poweroff, loggen, netzwerk aufräumen, done
            # exit status alles andere: error loggen, netzwerk aufräumen, done
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
            if Path(f"/dev/vmm/{self.name}").exists():
                await self._bhyvectl_destroy()
            if self._on_exit is not None:
                self._on_exit(self.name)
