from .errors import AnlasserBhyveControllerError

# NVMe disks are assigned to PCI slots 4-6.
# Slot = DISK_BASE_SLOT + order value.
# This range is chosen because UEFI guests (especially Windows)
# expect disk devices in slots 3-6. Slot 3 is reserved for ISO/CD.
DISK_BASE_SLOT = 4
MAX_DISKS = 3


def parse_disk_sections(disks_dict):
    """Parse and validate [VM.disks.*] sections.
    Returns a list of disk dicts sorted by slot order.
    """
    disks = []
    for disk_name, disk_config in disks_dict.items():
        try:
            order = disk_config["order"]
            storage_path = disk_config["storage_path"]
        except KeyError as e:
            raise AnlasserBhyveControllerError(
                f"Disk '{disk_name}' is missing required key {e}"
            )
        disks.append({"name": disk_name, "storage_path": storage_path, "order": order})

    if len(disks) > MAX_DISKS:
        raise AnlasserBhyveControllerError(
            f"Too many disks ({len(disks)}), maximum is {MAX_DISKS}"
        )

    # Check for duplicate order values
    orders = [d["order"] for d in disks]
    if len(orders) != len(set(orders)):
        raise AnlasserBhyveControllerError("Duplicate disk order values")

    for d in disks:
        if d["order"] < 0 or d["order"] >= MAX_DISKS:
            raise AnlasserBhyveControllerError(
                f"Disk '{d['name']}': order must be between 0 and {MAX_DISKS - 1}"
            )

    return sorted(disks, key=lambda d: d["order"])


def build_disk_args(disks):
    """Build bhyve -s arguments for NVMe disks.
    Expects a sorted list of disk dicts (from parse_disk_sections).
    """
    # FIXME: are sectsz=4096 and other NVMe parameters optimal?
    args = []
    for disk in disks:
        slot = DISK_BASE_SLOT + disk["order"]
        args.extend(["-s", f"{slot},nvme,{disk['storage_path']},sectsz=4096"])
    return args
