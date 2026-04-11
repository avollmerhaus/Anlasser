import pytest

from pathlib import Path

from anlasser.bhyve_controller import AnlasserBhyveController
from anlasser.bhyve_controller_config import build_bhyve_command
from anlasser.errors import AnlasserBhyveControllerError

from conftest import (
    _generate_config_disk_section,
    _write_config_file,
)


# Intention: verify a valid config round-trips into a correct bhyve command.
# Expected outcome: command includes correct bootrom, disk slot, NIC slot, and VM name.
def test_load_config_and_build_bhyve_command(tmp_path):
    vm_name = "testvm1"
    vm = AnlasserBhyveController(vm_name)
    expected_uefi_vars_path = f"/tank/VMs/{vm_name}/BHYVE_UEFI_VARS.fd"
    config_path = _write_config_file(tmp_path, vm_name)

    vm.load_config(config_path)
    # Simulate tap device creation so build_bhyve_command can include NIC args
    vm.tapdevs[0] = "tap0"
    command = build_bhyve_command(vm)

    assert command[-1] == vm_name
    assert (
        f"bootrom,/usr/local/share/uefi-firmware/BHYVE_UEFI_CODE.fd,{expected_uefi_vars_path}"
        in command
    )
    # NVMe disk on slot 4 (order 0)
    assert f"4,nvme,/tank/VMs/{vm_name}/disk0.img,sectsz=4096" in command
    # NIC on slot 7:0
    assert "7:0,virtio-net,tap0" in command


# Intention: verify that a config missing required general keys is rejected.
# Expected outcome: load_config raises AnlasserBhyveControllerError.
def test_load_config_missing_key(tmp_path):
    vm_name = "testvm1"
    vm = AnlasserBhyveController(vm_name)
    config_path = Path(tmp_path, f"{vm_name}.toml")
    config_path.write_text(
        '[VM.general]\nname = "testvm1"\n\n[VM.vnc]\nvnc_port = 5900\n'
    )

    with pytest.raises(AnlasserBhyveControllerError):
        vm.load_config(config_path)


# Intention: verify multiple disks are parsed, sorted by order, and assigned correct slots.
# Expected outcome: three disks on slots 4, 5, 6 in order.
def test_load_config_multiple_disks(tmp_path):
    vm_name = "testvm1"
    vm = AnlasserBhyveController(vm_name)
    config_path = _write_config_file(
        tmp_path,
        vm_name,
        disks=[
            _generate_config_disk_section(
                "disk0", storage_path="/tank/VMs/testvm1/disk0.img", order=0
            ),
            _generate_config_disk_section(
                "disk1", storage_path="/tank/VMs/testvm1/disk1.img", order=1
            ),
            _generate_config_disk_section(
                "disk2", storage_path="/tank/VMs/testvm1/disk2.img", order=2
            ),
        ],
    )

    vm.load_config(config_path)
    vm.tapdevs[0] = "tap0"
    command = build_bhyve_command(vm)

    assert len(vm.disks) == 3
    # Verify disks are sorted by order
    assert vm.disks[0]["order"] == 0
    assert vm.disks[1]["order"] == 1
    assert vm.disks[2]["order"] == 2
    # Verify slot assignments in bhyve command
    assert "4,nvme,/tank/VMs/testvm1/disk0.img,sectsz=4096" in command
    assert "5,nvme,/tank/VMs/testvm1/disk1.img,sectsz=4096" in command
    assert "6,nvme,/tank/VMs/testvm1/disk2.img,sectsz=4096" in command
