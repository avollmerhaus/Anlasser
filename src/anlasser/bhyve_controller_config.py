import logging
import tomllib
from pathlib import Path

from .bhyve_controller_storage import build_disk_args, parse_disk_sections
from .errors import AnlasserBhyveControllerError


def _parse_nics_sections(nics_dict):
    nics = {}
    for i, (nic_name, nic_config) in enumerate(sorted(nics_dict.items())):
        try:
            bridge = nic_config["bridge"]
        except KeyError:
            raise AnlasserBhyveControllerError(
                f"NIC '{nic_name}' is missing required key 'bridge'"
            )
        mac = nic_config.get("mac", None)
        nics[i] = {"bridge": bridge, "mac": mac}
    return nics


def load_bhyve_controller_config(vm, config_path):
    logging.info(f"Trying to load bhyve controller config from {config_path}")
    try:
        with open(config_path, "rb") as f:
            config = tomllib.load(f)

        general = config["VM"]["general"]
        vm.name = general["name"]
        vm.memory_mb = general["memory_mb"]
        vm.cpu_sockets = general["cpu_sockets"]
        vm.cpu_cores = general["cpu_cores"]
        vm.cpu_threads = general["cpu_threads"]
        vm.uefi_vars_storage_path = general["uefi_vars_storage_path"]
        vm.boot_iso_path = general.get("boot_iso_path", None)
        shutdown_timeout = general.get("shutdown_timeout", None)
        if shutdown_timeout is not None:
            vm.shutdown_timeout = float(shutdown_timeout)

        vnc = config["VM"]["vnc"]
        # FIXME: Handle vnc ports internally, stop bothering the user!
        vm.vnc_port = vnc["vnc_port"]
        vm.vnc_wait_connect = vnc.get("vnc_wait_connect", False)
        vm.vnc_kbd_layout = vnc.get("vnc_kbd_layout", None)
    except FileNotFoundError:
        raise AnlasserBhyveControllerError(f"Config file not found: {config_path}")
    except KeyError as e:
        raise AnlasserBhyveControllerError(
            f"Error loading bhyve controller config at {config_path}, missing key {e}"
        )
    except (ValueError, tomllib.TOMLDecodeError) as e:
        raise AnlasserBhyveControllerError(
            f"Error loading bhyve controller config at {config_path}, {e}"
        )

    if vm.name != Path(config_path).stem:
        raise AnlasserBhyveControllerError(
            f"Error loading bhyve controller config file at {config_path}, file name / VM name mismatch"
        )

    # Parse disk sections ([VM.disks.*])
    disks_dict = config["VM"].get("disks", {})
    vm.disks = parse_disk_sections(disks_dict)

    # Parse NIC sections ([VM.nics.*])
    # Zero NIC sections is valid (VM with no networking).
    # We use integer indices as internal keys (not the user-supplied section names).
    nics_dict = config["VM"].get("nics", {})
    vm.nics = _parse_nics_sections(nics_dict)

    logging.info(f"VM {vm.name}: Config loaded from {config_path}")


def build_bhyve_command(vm):
    """Build the full bhyve command list from vm config.
    Call this after tap devices have been created (vm.tapdevs must be populated).
    """
    # FIXME: Maybe the hardcoded stuff here should probably be configurable, too.
    vnc_listen = f"127.0.0.1:{vm.vnc_port}"
    vnc_resolution = "w=1600,h=900"
    vnc_config = f"tcp={vnc_listen},{vnc_resolution}"
    if vm.vnc_wait_connect:
        vnc_config += ",wait"

    # Keep in mind that slot numbers for `-s` options are magic in the sense that
    # guest OS, especially Windows, might be picky about what device is in what slot.
    # Slot layout:
    #  0:    hostbridge
    #  3:    ISO/CD (ahci-cd, when present)
    #  4-6:  NVMe disks (order 0 → slot 4, order 1 → slot 5, order 2 → slot 6)
    #  7:N:  NICs (multi-function on slot 7)
    #  8:    framebuffer (VNC)
    #  9:    xhci tablet
    #  10:   virtio-rnd
    #  31:   LPC
    command = [
        "bhyve",
        # We intentionally do NOT pass "-A" (ACPI tables) here.
        # Since FreeBSD 15.0, ACPI tables are always generated (acpi_tables defaults to true).
        # "-A" is a no-op now and was removed from the man page.
        # See https://github.com/freebsd/freebsd-src/commit/6a0e7f908802b86ca5d1c0b3c404b8391d0f626e
        # We intentionally do NOT pass "-D" here.
        # "-D" only destroys /dev/vmm/<name> on guest-initiated poweroff (exit code 1).
        # It does not cover reboots (0), errors (4), or external termination (SIGTERM).
        # Instead, we unconditionally run `bhyvectl --destroy` after bhyve exits.
        # See AnlasserBhyveController.run() for the cleanup logic.
        ####
        # Force vCPU to exit when the guest issues a PAUSE instruction.
        "-P",
        ####
        # Yield vCPU when the guest issues HLT instructions. The vCPU uses 100% host CPU otherwise.
        "-H",
        ####
        "-w",
        # Ignore access to unimplemented MSRs (sets x86.strictmsr=false).
        # The man page says "debug purposes", but in practice this is needed for Linux guests.
        # Without it, bhyve injects #GP faults on unimplemented MSR accesses, which can crash
        # guests depending on hardware/kernel combination (e.g. AMD Ryzen, FreeBSD bug 235010).
        # Modern Linux kernels use rdmsr_safe/wrmsr_safe for some MSRs but not all.
        # See https://forums.freebsd.org/threads/what-will-i-lose-if-i-dont-use-the-bhyve-a-h-p-w-s-flags.92420/
        ####
        "-c",
        f"sockets={vm.cpu_sockets},cores={vm.cpu_cores},threads={vm.cpu_threads}",
        ####
        "-m",
        f"{vm.memory_mb}M",
        ####
        # Keep VM clock in UTC. I guess Windows will need to set that registry option, I don't care much.
        "-u",
        ####
        # The PCIe root bridge I guess?
        "-s",
        "0,hostbridge",
        ####
        # LPC PCI-ISA bridge with COM1,2,3,4 16550 serial ports and boot ROM.
        "-s",
        "31,lpc",
    ]

    # NVMe disks (slots 4-6)
    command.extend(build_disk_args(vm.disks))

    command.extend(
        [
            ####
            "-s",
            f"8,fbuf,{vnc_config}",
            ####
            # Host and guest mouse might develop an offset, tablet support mitigates that.
            "-s",
            "9,xhci,tablet",
            ####
            # I've seen reports about VMs that were totally starved of randomness w/o virtio-rnd.
            "-s",
            "10,virtio-rnd",
            ####
            # FIXME: Serial console support is planned but not yet implemented.
            # Needs investigation into how this interacts with bhyve output redirection.
            # Alternatives: com1,tcp=127.0.0.1:<port> or logging to a file.
            # '-l', 'com1,stdio',
            ####
            # fwcfg=qemu enables the QEMU-style firmware config interface, which is required
            # for bootindex support (explicit boot order control). Requires edk2-bhyve >= g202408.
            # Previously disabled due to a CPU core detection bug on Intel Atom C3558 (2024-08).
            # Re-enabled 2026-04: not reproducible on other hardware with edk2-bhyve g202508.
            #
            # We use BHYVE_UEFI_CODE.fd (firmware code only), not BHYVE_UEFI.fd (combined code+vars).
            # Since we always pass a separate per-VM vars file (opened read-write by bhyve for
            # the guest to persist boot order etc.), the combined image is wrong here — it would
            # double the vars region. See the bhyve(8) man page bootrom examples.
            "-l",
            f"bootrom,/usr/local/share/uefi-firmware/BHYVE_UEFI_CODE.fd,{vm.uefi_vars_storage_path},fwcfg=qemu",
        ]
    )

    # NIC slots use multi-function on slot 7 (7:0, 7:1, ...).
    # This is the approach shown in the bhyve(8) man page.
    for i, (nic_name, nic) in enumerate(vm.nics.items()):
        tap_config = vm.tapdevs[nic_name]
        if nic["mac"]:
            tap_config += f",mac={nic['mac']}"
        command.extend(["-s", f"7:{i},virtio-net,{tap_config}"])

    if vm.vnc_kbd_layout is not None:
        vnc_kbd_layout_path = Path(f"/usr/share/bhyve/kbdlayout/{vm.vnc_kbd_layout}")
        if vnc_kbd_layout_path.is_file():
            # For VNC clients w/o QEMU extended key event support.
            # bhyve expects the layout name, not the full path.
            command.extend(["-K", vm.vnc_kbd_layout])
        else:
            logging.warning(
                f"No VNC keyboard layout file at {vnc_kbd_layout_path}, ignoring layout"
            )

    if vm.boot_iso_path is not None:
        # Some OS seem to be picky and want disk devices or dvds only in slots 3 to 6.
        command.extend(["-s", f"3,ahci-cd,{vm.boot_iso_path}"])

    # VM name always has to be the last component of the bhyve command
    command.append(vm.name)

    return command
