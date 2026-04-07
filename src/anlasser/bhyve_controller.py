import asyncio
import logging
from pathlib import Path

from .bhyve_controller_config import load_bhyve_controller_config, build_bhyve_command
from .bhyve_controller_networking import add_tap, destroy_tap

from .errors import AnlasserBhyveControllerError


class AnlasserBhyveController:
    """Bhyve VM controller."""

    def __init__(self, name):
        # VM config (public)
        self.name = name
        self.memory_mb = None
        self.cpu_sockets = None
        self.cpu_cores = None
        self.cpu_threads = None
        self.storage_path = None
        self.uefi_vars_storage_path = None
        self.nics = {}
        self.tapdevs = {}
        self.vnc_port = None
        self.vnc_kbd_layout = None
        self.vnc_wait_connect = None
        self.iso_path = None
        self.shutdown_timeout = 90

    def load_config(self, config_path):
        load_bhyve_controller_config(self, config_path)

    async def _destroy_vmm_device_node(self):
        # bhyve does not clean up /dev/vmm/<name> on exit, regardless of exit code.
        # Even with "-D", only guest-initiated poweroff (rc=1) is covered.
        # We must always run bhyvectl --destroy to prevent stale device nodes
        # that would block future starts of this VM.
        vmm_path = Path(f"/dev/vmm/{self.name}")
        if not vmm_path.exists():
            logging.info(
                f"VM {self.name}: VMM device node {vmm_path} not found, ignoring"
            )
            return
        logging.info(f"VM {self.name}: Destroying VMM device node {vmm_path}")
        proc = await asyncio.create_subprocess_exec(
            "bhyvectl",
            f"--destroy",
            f"--vm={self.name}",
        )
        rc = await proc.wait()
        if rc != 0:
            logging.error(f"VM {self.name}: bhyvectl --destroy exited with rc={rc}")

    async def _network_setup(self):
        """Create tap devices and add them to their bridges.
        Raises on failure — caller should not start bhyve.
        """
        for nic_name, nic in self.nics.items():
            self.tapdevs[nic_name] = await add_tap(self.name, nic["bridge"])

    async def _network_teardown(self):
        for tapdev in self.tapdevs.values():
            try:
                await destroy_tap(tapdev)
            except RuntimeError as exc:
                logging.error(f"VM {self.name}: {exc}")

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

        # Bhyve exit codes (man bhyve):
        bhyve_exit = {
            0: "reboot",
            1: "ordinary shutdown",
            2: "halted (abnormal)",
            3: "triple fault (abnormal)",
            4: "error (abnormal)",
        }

        try:
            if Path(f"/dev/vmm/{self.name}").exists():
                raise AnlasserBhyveControllerError(
                    f"VM {self.name}: Refusing to start, device node /dev/vmm/{self.name} already exists. "
                    "This indicates a stale or still-running VM context. If you are sure the VM is not running, "
                    f"clean it up with: bhyvectl --destroy --vm={self.name}"
                )

            await self._network_setup()
            bhyve_command = build_bhyve_command(self)

            logging.info(f"Starting VM {self.name}")
            while True:
                proc = await asyncio.create_subprocess_exec(
                    *bhyve_command,
                    start_new_session=True,
                )
                logging.info(f"VM {self.name}: Subprocess started, pid={proc.pid}")
                rc = await proc.wait()
                logging.info(
                    f"VM {self.name}: Bhyve exit {rc}, "
                    + bhyve_exit.get(rc, "unknown (abnormal)")
                )
                if rc > 0:
                    break
                # Prevent runaway load should we somehow get into an infinite loop,
                # with bhyve constantly exiting with status 0.
                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            logging.info(f"VM {self.name} pid={proc.pid} cancelled, shutting down")
            await self._stop_bhyve(proc)
            return
        finally:
            await self._destroy_vmm_device_node()
            await self._network_teardown()
