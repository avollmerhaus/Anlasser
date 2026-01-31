import asyncio
import logging

class AnlasserVM:

    def __init__(self, name, on_exit):
        self.name = name
        # self.config = self._load_config(name)
        self.bhyve_command = ["sleep", "4"]
        self._bootstrap_done = False
        self._on_exit = on_exit

    async def _network_setup(self):
        if self._bootstrap_done:
            return True

        # We probably need to wait a while for the tap device to show up here,
        # with a timeout. See previous Anlasser version.
        # Alternatively, we could supply bhyve with a precreated tap device.
        # What about the MAC? And what about TOCTOU?
        # Maybe another task, even a 3rd party software, could open a tap device!
        logging.info(f"VM {self.name}: Creating tap device?")
        logging.info(f"VM {self.name}: Adding tap device to bridge")
        proc = await asyncio.create_subprocess_exec("echo", f"VM {self.name}: ifconfig bringup")
        rc = await proc.wait()
        if rc > 0:
            # Originally I tried to shut the VM down in case I was unable to add a tap device to a bridge,
            # but that just complicates things.
            # Sending SIGTERM to bhyve shortly after starting while the VM might still be booting probably
            # won't do us any good. So log the error and let the user deal with it.
            logging.error(f"VM {self.name}: Failed to add tap device to bridge")
            return False
        self._bootstrap_done = True
        return True

    async def _network_teardown(self):
        if not self._bootstrap_done:
            logging.info(f"VM {self.name}: No network teardown necessary")
            return True

        proc = await asyncio.create_subprocess_exec("echo", f"VM {self.name}: ifconfig teardown")
        rc = await proc.wait()
        if rc > 0:
            logging.error(f"VM {self.name}: Failed to tear down tap device")
            return False
        return True

    async def _stop_bhyve(self, proc, timeout=15.0):
        if proc is None or proc.returncode is not None:
            logging.warning(
                f"VM {self.name}: Not running (proc={'none' if proc is None else 'done'}, rc={None if proc is None else proc.returncode})"
            )
            return

        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout)
        except asyncio.TimeoutError:
            logging.error(f"VM {self.name}: Shutdown timeout expired, killing VM bhyve")
            proc.kill()
            # kill should prevent proc.wait() not returning,
            # but maybe we want to make sure we're not stuck here in the future.
            await proc.wait()

    async def run(self):
        proc = None
        try:
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
            if rc == 1:
                logging.info(f"VM {self.name}: poweroff, exit code {rc}")
            else:
                logging.info(f"VM {self.name}: crashed, exit code {rc}")
        except asyncio.CancelledError:
            logging.info(
                f"Shutting down VM {self.name} pid={proc.pid}"
            )
            await self._stop_bhyve(proc)
            raise
        finally:
            await self._network_teardown()
            if self._on_exit is not None:
                self._on_exit(self.name)
