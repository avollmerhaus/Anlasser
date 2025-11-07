import atexit
import configparser
import logging
import subprocess
from pathlib import Path
from time import sleep, time

from anlasser.AnlasserErrors import VMConfigError

# A word of warning to whomever might try to redesign this code.
# Do not try and wait for the bhyve subprocess from inside a signal handler.
# An early implementation of Anlasser tried to do this to no avail.
# Invoking a signal handler "freezes" the normal execution of the program,
# resuming only after the handler is done.
# In a way, executing a signal handler while the main program freezes is like threading.
# So the main "thread" is already waiting in bhyve subprocess `communicate()`, but it's suspended.
# Having one suspended `communicate()` going and then starting another one from inside the signal handler is a
# recipe for trouble. I ran into weird deadlocks.
# See https://github.com/python/cpython/issues/82388


class AnlasserVM:
    def __init__(self, logfile_object=None):
        # VM config (public)
        self.name = None
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

        # Public object state
        self.shutdown_flag = False

        # Private object state
        self._network_setup_done = False
        self._current_bhyve_proc = (
            None  # used only for testing, maybe there is a better way?
        )

        # I wanted a single place to specify and explain the arguments we're using to start subprocesses
        # from this class, bhyve itself plus ifconfig and bhyvectl.
        # - start_new_session:
        #   We need to control signal delivery to our subprocesses, normally the OS would deliver signals like
        #   SIGINT to all of our subprocesses via the process group or session.
        #   That can be bad for us, for example when SIGINT hits ifconfig or bhyvectl when they're just about to
        #   do their job. Using `start_new_session=True` places these subprocesses into a separate group and session,
        #   preventing the OS from propagating signals to our subprocesses.
        # - stdout, stderr:
        #   When stdout & stderr is set to `None`, `subprocess.Popen` simply "doesn't do any redirection".
        #   If not `None`, logfile_object has to be a "file-like" object that implements `write()`.
        self._subprocess_default_args = {
            "stdout": logfile_object,
            "stderr": logfile_object,
            "start_new_session": True,
        }

        atexit.register(self._cleanup)

    def _cleanup(self):
        # The exit function could be invoked when no config was loaded or Bhyve was never started.
        if Path(f"/dev/vmm/{self.name}").exists():
            self._bhyvectl_destroy()
        if self._network_setup_done is True:
            self._tap_operation("destroy", self.tapdev)

    def load_config(self, config_path):
        logging.info(f"Trying to load config from {config_path}")
        # We don't need to check if the config file actually exists.
        # config.read() will return an empty config for a nonexistent file, so we'll run into the KeyError handler.
        try:
            config = configparser.ConfigParser()
            config.read(config_path)

            self.name = config["VM"]["name"]
            self.memory_mb = config["VM"]["memory_mb"]
            self.cpu_sockets = config["VM"]["cpu_sockets"]
            self.cpu_cores = config["VM"]["cpu_cores"]
            self.cpu_threads = config["VM"]["cpu_threads"]
            self.storage_path = config["VM"]["storage_path"]
            self.uefi_vars_storage_path = config["VM"]["uefi_vars_storage_path"]
            # If no mac is set we let Bhyve generate one.
            self.mac = config["VM"].get("mac", None)
            # FIXME: Handle tap devices internally, stop bothering the user!
            self.tapdev = config["VM"]["tapdev"]
            self.bridge = config["VM"]["bridge"]
            # FIXME: Handle vnc ports internally, stop bothering the user!
            self.vnc_port = config["VM"]["vnc_port"]
            # vnc_wait_connect has to be a string, we want to use str.lower later in the code.
            self.vnc_wait_connect = config["VM"].get("vnc_wait_connect", "False")
            self.vnc_kbd_layout = config["VM"].get("vnc_kbd_layout", None)
            self.iso_path = config["VM"].get("iso_path", None)
        except KeyError as e:
            raise VMConfigError(
                f"Error loading VM config at {config_path}, missing key {e}"
            )

        if self.name != Path(config_path).stem:
            raise VMConfigError(
                f"Error loading VM config file at {config_path}, file name / VM name mismatch"
            )

        # FIXME: we'll need some validation of the configuration data here.
        # Is the VM name unique?
        # is the VNC port unique?
        # Is the MAC address unique?
        # Does the ISO path actually exist?
        # Does the bridge exist?
        # Is the storage_path assigned to any other VM?
        # Is the tapdev unique?
        # The question is probably which kind of things we'll want to allow,
        # for example configuring 2 VMs with the same backing storage is fine in some situations.
        # Should an untenable configuration be detected, raise VMConfigError or ValueError.
        # We'll also need tests to verify that.

        tap_config = f"{self.tapdev},mac={self.mac}" if self.mac else f"{self.tapdev}"

        # Maybe the hardcoded stuff here should probably be configurable, too.
        vnc_listen = f"127.0.0.1:{self.vnc_port}"
        vnc_resolution = "w=1600,h=900"
        if self.vnc_wait_connect.lower() in ("y", "yes", "true", "on", "1"):
            vnc_wait_parameter = ",wait"
        else:
            vnc_wait_parameter = ""
        vnc_config = f"tcp={vnc_listen},{vnc_resolution}{vnc_wait_parameter}"

        self.bhyve_command = [
            # Keep in mind that slot numbers for `-s` options are magic in the sense that
            # guest OS, especially windows, might be picky about what device is in what slot.
            # I tried to copy the slot numbers from `churchers/vm-bhyve`.
            "bhyve",
            "-P",  # Force vCPU to exit when the guest issues a PAUSE instruction.
            "-A",  # Generate ACPI tables inside the guest.
            "-D",  # Destroy the VM on guest-initiated shutdown.
            "-H",  # Yield vCPU when the guest issues HLT instructions. The vCPU uses 100% host CPU otherwise.
            "-w",  # Ignore access to "unspecified registers", vm-bhyve uses this. But "man bhyve" says "experimental"?
            "-c",
            f"sockets={self.cpu_sockets},cores={self.cpu_cores},threads={self.cpu_threads}",
            "-m",
            f"{self.memory_mb}M",
            "-u",  # Keep VM clock in UTC. I guess Windows will need to set that registry option, I don't care much.
            "-s",
            "0,hostbridge",  # The PCIe root bridge I guess?
            "-s",
            "31,lpc",  # LPC PCI-ISA bridge with COM1,2,3,4 16550 serial ports and boot ROM.
            "-s",
            # The options "direct,nocache" might be interesting.
            # Benchmarking lead to horrid results.
            # But in theory, both the host and the guest have a disk cache. It's a waste to engage them both I guess?
            # See "man bhyve" for (very terse) info on direct,nocache".
            f"4,nvme,{self.storage_path},sectsz=4096",  # FIXME: are these parameters optimal?
            "-s",
            f"5,virtio-net,{tap_config}",
            "-s",
            f"6,fbuf,{vnc_config}",
            "-s",
            "8,xhci,tablet",  # Host and guest mouse might develop an offset, tablet support mitigates that.
            "-s",
            "9,virtio-rnd",  # I've seen reports about VMs that were totally starved of randomness w/o virtio-rnd.
            # '-l', 'com1,stdio',  # FIXME: this mixes bhyve output and VM output on stdout.
            # Now comes the bootrom.
            # Theoretically, appending ",fwcfg=qemu" should have some benefits over the bhyve interface,
            # for example it might get the bootindex option working.
            # But all I got out of that were problems with unstable tsc clocksource.
            # I'm not sure how bad that really is, but it seems to be linked to problem reports.
            # So let's stay away from the newer fwcfg for now.
            # Update 13.06.24: clocksource problems seem to be unrelated to `fwcfw=qemu`.
            # Update 08.08.24: when testing with an Intel Atom C3558, `fwcfw=qemu` lead to problems with just one
            # CPU core being detected inside the VM (tested with Linux kernel 6.1 and 6.11).
            "-l",
            f"bootrom,/usr/local/share/uefi-firmware/BHYVE_UEFI.fd,{self.uefi_vars_storage_path}",
        ]

        if self.vnc_kbd_layout is not None:
            vnc_kbd_layout_path = Path(
                f"/usr/share/bhyve/kbdlayout/{self.vnc_kbd_layout}"
            )
            if vnc_kbd_layout_path.is_file():
                # For VNC clients w/o QEMU extended key event support
                self.bhyve_command.extend(["-K", f"{vnc_kbd_layout_path}"])
            else:
                logging.warning(
                    f"No VNC keyboard layout file at {vnc_kbd_layout_path}, ignoring layout"
                )
                # Should we make this fatal? Without more modifications, this prevents testing on Linux
                # raise VMConfigError(f"No VNC keyboard layout file at {vnc_kbd_layout_path}")

        if self.iso_path is not None:
            # Some OS seem to be picky and want disk devices or dvds only in slots 3 to 6.
            self.bhyve_command.extend(["-s", f"3,ahci-cd,{self.iso_path}"])

        # VM name always has to be the last component of the bhyve command
        self.bhyve_command.append(self.name)

        logging.info(
            f"Successfully loaded config for VM {self.name} from {config_path}"
        )

    def _wait_for_tap_device_creation(self, tapdev_name, timeout=5):
        deadline = time() + timeout
        logging.info(f"Waiting {timeout}s for tap device {tapdev_name} to appear")
        while time() < deadline:
            proc = subprocess.run(
                ["ifconfig", "-l"],
                capture_output=True,
                encoding="utf-8",
                # Can't use `**vm._subprocess_default_args` here, `stdout=` doesn't mix with `catpure_output=True`!
                start_new_session=True,
            )
            available_interfaces = proc.stdout.split()
            if tapdev_name in available_interfaces:
                logging.info(f"{tapdev_name}: tap device has been created")
                return True
            sleep(0.2)
        raise TimeoutError(f"Timeout waiting for tap device {tapdev_name} to appear")

    def _tap_operation(self, action, tapdev_name, bridge_name=None):
        """
        We should add capability for multiple tap devices here.
        Maybe simply loop through them.

        :param action: "add" or "destroy"
        """
        ifconfig_commands = {
            "add": ["ifconfig", bridge_name, "addm", tapdev_name],
            "destroy": ["ifconfig", tapdev_name, "destroy"],
        }

        command = ifconfig_commands[action]
        logging.info(f"Running command: {command}")
        try:
            subprocess.check_call(command, **self._subprocess_default_args)
        except subprocess.CalledProcessError as err:
            # Originally I tried to shut the VM down in case I was unable to add a tap device to a bridge,
            # but that just complicates things.
            # Sending SIGTERM to bhyve mere seconds after starting while the VM might still be booting probably
            # won't do us any good. So log the error and let the user deal with the problem,
            # they can shut the VM down if so desired.
            logging.error(f"Error running ifconfig: {err}")
            return False
        else:
            if action == "add":
                # FIXME: this assumes a single tap device
                self._network_setup_done = True
                return True

    def _bhyve_proc_generator(self):
        while True:
            logging.info(f"Invoking Bhyve using command {self.bhyve_command}")
            bhyve_proc = subprocess.Popen(
                self.bhyve_command, **self._subprocess_default_args
            )
            yield bhyve_proc

    def _busy_loop(self, bhyve_proc):
        while bhyve_proc.poll() is None:
            sleep(0.2)
            if self.shutdown_flag is True:
                logging.info("Shutdown flag set, initiate graceful bhyve shutdown")
                self._shutdown_bhyve(bhyve_proc)
                logging.info(f"Bhyve stopped with exit code {bhyve_proc.returncode}")
        return bhyve_proc.returncode

    def _shutdown_bhyve(self, bhyve_proc, timeout=300):
        # FIXME: the timeout should be configurable from the VM config.
        # But how do we get AnlasserAgent to respect that as well, without reading the config from there?
        bhyve_proc.terminate()
        graceful = False
        try:
            bhyve_proc.wait(timeout)
            logging.info("Graceful Bhyve shutdown complete")
            graceful = True
        except subprocess.TimeoutExpired:
            logging.info(
                f"Graceful shutdown did not quit within {timeout}s, applying force"
            )
            bhyve_proc.kill()
        finally:
            bhyve_proc.communicate()
            return graceful

    def _bhyvectl_destroy(self):
        # As long as the "--vm=(name)" parameter is present and the name has a device node at /dev/vmm/(name),
        # bhyvectl will gladly accept whatever bullshit you throw at it and __only__ raise a syntax error
        # if the bullshit has two dashes in front of it. So pay good attention when modifying the command!
        # Forgetting some dashes in front of a command may leave you scratching your head.
        command = ["bhyvectl", "--destroy", f"--vm={self.name}"]
        logging.info(f"Running command: {command}")
        try:
            subprocess.check_call(command, **self._subprocess_default_args)
        except subprocess.CalledProcessError as err:
            logging.error(f"Error running bhyvectl: {err}")

    def run(self):
        """
        This function is the external interface presented to consumers of this lib for starting bhyve.
        It should be called after only after loading a configuration using load_config.

        :return: 0 if bhyve terminated successfully, 1 otherwise.
        """
        if self.bhyve_command is None:
            raise VMConfigError(
                "run() invoked w/o config. You have to load a config using load_config() first."
            )

        if not Path(self.uefi_vars_storage_path).is_file():
            raise VMConfigError(
                f"No EFIVARS file at {self.uefi_vars_storage_path}, copy it from /usr/local/share/uefi-firmware/BHYVE_UEFI_VARS.fd"
            )

        bhyve_exit_code = None
        for bhyve_proc in self._bhyve_proc_generator():

            if self._network_setup_done is False:
                self._wait_for_tap_device_creation(self.tapdev)
                self._tap_operation("add", self.tapdev, self.bridge)

            bhyve_exit_code = self._busy_loop(bhyve_proc)

            # Bhyve exit codes (man bhyve):
            # 0 - reboot
            # 1 - power off
            # 2 - halted
            # 3 - triple fault
            # 4 - exited due to an error

            # Bhyve exit code == 1 is our ordinary shutdown, let's map that to our exit code 0.
            # For everything > 1, let's quit with a 1.
            # I don't think a more meaningful exit code is worth the hassle here.
            # We'll simply say "something went wrong here" and that's that.

            if bhyve_exit_code == 0:
                logging.info(
                    "Bhyve exit status 0 (ordinary reboot), starting new Bhyve process"
                )
            elif bhyve_exit_code == 1:
                logging.info(
                    "Bhyve exit status 1 (ordinary shutdown), watcher shutting down"
                )
                return 0
            else:
                logging.info(
                    f"Bhyve exit status {bhyve_exit_code}, watcher shutting down"
                )
                return 1
