import configparser
import logging
from pathlib import Path

from .errors import AnlasserBhyveControllerError


def load_bhyve_controller_config(vm, config_path):
    logging.info(f"Trying to load bhyve controller config from {config_path}")
    # We don't need to check if the config file actually exists.
    # config.read() will return an empty config for a nonexistent file, so we'll run into the KeyError handler.
    try:
        config = configparser.ConfigParser()
        config.read(config_path)

        vm.name = config["VM"]["name"]
        vm.memory_mb = config["VM"]["memory_mb"]
        vm.cpu_sockets = config["VM"]["cpu_sockets"]
        vm.cpu_cores = config["VM"]["cpu_cores"]
        vm.cpu_threads = config["VM"]["cpu_threads"]
        vm.storage_path = config["VM"]["storage_path"]
        vm.uefi_vars_storage_path = config["VM"]["uefi_vars_storage_path"]
        # FIXME: Handle vnc ports internally, stop bothering the user!
        vm.vnc_port = config["VM"]["vnc_port"]
        # vnc_wait_connect has to be a string, we want to use str.lower later in the code.
        vm.vnc_wait_connect = config["VM"].get("vnc_wait_connect", "False")
        vm.vnc_kbd_layout = config["VM"].get("vnc_kbd_layout", None)
        vm.iso_path = config["VM"].get("iso_path", None)
        shutdown_timeout = config["VM"].get("shutdown_timeout", None)
        if shutdown_timeout is not None:
            vm.shutdown_timeout = float(shutdown_timeout)
    except KeyError as e:
        raise AnlasserBhyveControllerError(
            f"Error loading bhyve controller config at {config_path}, missing key {e}"
        )
    except ValueError as e:
        raise AnlasserBhyveControllerError(
            f"Error loading bhyve controller config at {config_path}, invalid value {e}"
        )

    if vm.name != Path(config_path).stem:
        raise AnlasserBhyveControllerError(
            f"Error loading bhyve controller config file at {config_path}, file name / VM name mismatch"
        )

    # Parse NIC sections (NIC.0, NIC.1, ...)
    # Zero NIC sections is valid (VM with no networking).
    nic_sections = sorted(s for s in config.sections() if s.startswith("NIC."))
    vm.nics = {}
    for section in nic_sections:
        try:
            bridge = config[section]["bridge"]
        except KeyError:
            raise AnlasserBhyveControllerError(
                f"Error loading bhyve controller config at {config_path}, "
                f"section [{section}] is missing required key 'bridge'"
            )
        mac = config[section].get("mac", None)
        vm.nics[section] = {"bridge": bridge, "mac": mac}

    logging.info(f"Successfully loaded config for VM {vm.name} from {config_path}")


def build_bhyve_command(vm):
    """Build the full bhyve command list from vm config.
    Call this after tap devices have been created (vm.tapdevs must be populated).
    """
    # FIXME: Maybe the hardcoded stuff here should probably be configurable, too.
    vnc_listen = f"127.0.0.1:{vm.vnc_port}"
    vnc_resolution = "w=1600,h=900"
    vnc_config = f"tcp={vnc_listen},{vnc_resolution}"
    if vm.vnc_wait_connect.lower() in ("y", "yes", "true", "on", "1"):
        vnc_config += ",wait"

    # Keep in mind that slot numbers for `-s` options are magic in the sense that
    # guest OS, especially Windows, might be picky about what device is in what slot.
    # I tried to copy the slot numbers from `churchers/vm-bhyve`.
    command = [
        "bhyve",
        "-P",  # Force vCPU to exit when the guest issues a PAUSE instruction.
        "-A",  # Generate ACPI tables inside the guest.
        # We intentionally do NOT pass "-D" here.
        # "-D" only destroys /dev/vmm/<name> on guest-initiated poweroff (exit code 1).
        # It does not cover reboots (0), errors (4), or external termination (SIGTERM).
        # Instead, we unconditionally run `bhyvectl --destroy` after bhyve exits.
        # See AnlasserBhyveController.run() for the cleanup logic.
        "-H",  # Yield vCPU when the guest issues HLT instructions. The vCPU uses 100% host CPU otherwise.
        "-w",  # Ignore access to "unspecified registers", vm-bhyve uses this. But "man bhyve" says "debug"?
        "-c",
        f"sockets={vm.cpu_sockets},cores={vm.cpu_cores},threads={vm.cpu_threads}",
        "-m",
        f"{vm.memory_mb}M",
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
        f"4,nvme,{vm.storage_path},sectsz=4096",  # FIXME: are these parameters optimal?
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
        f"bootrom,/usr/local/share/uefi-firmware/BHYVE_UEFI.fd,{vm.uefi_vars_storage_path}",
    ]

    # NIC slots use multi-function on slot 5 (5:0, 5:1, ...).
    # This is the approach shown in the bhyve(8) man page.
    for i, (nic_name, nic) in enumerate(vm.nics.items()):
        tap_config = vm.tapdevs[nic_name]
        if nic["mac"]:
            tap_config += f",mac={nic['mac']}"
        command.extend(["-s", f"5:{i},virtio-net,{tap_config}"])

    if vm.vnc_kbd_layout is not None:
        vnc_kbd_layout_path = Path(f"/usr/share/bhyve/kbdlayout/{vm.vnc_kbd_layout}")
        if vnc_kbd_layout_path.is_file():
            # For VNC clients w/o QEMU extended key event support
            command.extend(["-K", f"{vnc_kbd_layout_path}"])
        else:
            logging.warning(
                f"No VNC keyboard layout file at {vnc_kbd_layout_path}, ignoring layout"
            )
            # Should we make this fatal? Without more modifications, this prevents testing on Linux
            # raise AnlasserInvalidVMConfigError(f"No VNC keyboard layout file at {vnc_kbd_layout_path}")

    if vm.iso_path is not None:
        # Some OS seem to be picky and want disk devices or dvds only in slots 3 to 6.
        command.extend(["-s", f"3,ahci-cd,{vm.iso_path}"])

    # VM name always has to be the last component of the bhyve command
    command.append(vm.name)

    return command
