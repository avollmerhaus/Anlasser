import pytest

from pathlib import Path

from anlasser.bhyve_controller import AnlasserBhyveController
from anlasser.bhyve_controller_config import build_bhyve_command
from anlasser.errors import AnlasserBhyveControllerError


def _vm_section(vm_name):
    """Just the [VM] section, no NICs. For tests that need custom NIC config."""
    return "\n".join(
        [
            "[VM]",
            f"name = {vm_name}",
            "memory_mb = 1024",
            "cpu_sockets = 1",
            "cpu_cores = 2",
            "cpu_threads = 1",
            f"storage_path = /tank/VMs/{vm_name}/{vm_name}.img",
            f"uefi_vars_storage_path = /tank/VMs/{vm_name}/BHYVE_UEFI_VARS.fd",
            "vnc_port = 5900",
        ]
    )


def _base_config(vm_name, extra=""):
    return _vm_section(vm_name) + "\n\n[NIC.0]\nbridge = bridge0\n" + extra


def test_load_config_and_build_bhyve_command(tmp_path):
    vm_name = "testvm1"
    vm = AnlasserBhyveController(vm_name)
    config_path = Path(tmp_path, f"{vm_name}.ini")
    uefi_vars_path = f"/tank/VMs/{vm_name}/BHYVE_UEFI_VARS.fd"
    config_path.write_text(_base_config(vm_name), encoding="utf-8")

    vm.load_config(config_path)
    # Simulate tap device creation so build_bhyve_command can include NIC args
    vm.tapdevs["NIC.0"] = "tap0"
    command = build_bhyve_command(vm)

    assert command[-1] == vm_name
    assert (
        f"bootrom,/usr/local/share/uefi-firmware/BHYVE_UEFI.fd,{uefi_vars_path}"
        in command
    )
    assert "5:0,virtio-net,tap0" in command


def test_load_config_parses_nics(tmp_path):
    vm_name = "testvm1"
    vm = AnlasserBhyveController(vm_name)
    config_path = Path(tmp_path, f"{vm_name}.ini")
    config_path.write_text(_base_config(vm_name), encoding="utf-8")

    vm.load_config(config_path)

    assert len(vm.nics) == 1
    assert vm.nics["NIC.0"]["bridge"] == "bridge0"


def test_load_config_zero_nics(tmp_path):
    vm_name = "testvm1"
    vm = AnlasserBhyveController(vm_name)
    config_path = Path(tmp_path, f"{vm_name}.ini")
    config_path.write_text(_vm_section(vm_name), encoding="utf-8")

    vm.load_config(config_path)

    assert vm.nics == {}


def test_load_config_nic_missing_bridge(tmp_path):
    vm_name = "testvm1"
    vm = AnlasserBhyveController(vm_name)
    config_path = Path(tmp_path, f"{vm_name}.ini")
    config_path.write_text(
        _vm_section(vm_name) + "\n\n[NIC.0]\nmac = 02:00:00:00:02:01\n",
        encoding="utf-8",
    )

    with pytest.raises(AnlasserBhyveControllerError):
        vm.load_config(config_path)


def test_load_config_missing_key(tmp_path):
    vm_name = "testvm1"
    vm = AnlasserBhyveController(vm_name)
    config_path = Path(tmp_path, f"{vm_name}.ini")
    config_path.write_text("[VM]\nname = testvm1\n", encoding="utf-8")

    with pytest.raises(AnlasserBhyveControllerError):
        vm.load_config(config_path)
