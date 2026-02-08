import pytest

from pathlib import Path

from anlasser.vm import AnlasserVM
from anlasser.errors import AnlasserVMError


def test_load_config_happy_path(vm_config_setup):
    vm_name = vm_config_setup.vm_name
    vm = AnlasserVM(vm_name, None)
    vm.load_config(vm_config_setup.config_file_path)

    assert vm.name == vm_name
    assert vm.memory_mb == "1024"
    assert vm.cpu_sockets == "1"
    assert vm.cpu_cores == "2"
    assert vm.cpu_threads == "1"
    assert vm.storage_path == f"/tank/VMs/{vm_name}/{vm_name}.img"
    assert vm.uefi_vars_storage_path == f"/tank/VMs/{vm_name}/BHYVE_UEFI_VARS.fd"
    assert vm.mac == "02:00:00:00:02:01"
    assert vm.tapdev == "tap0"
    assert vm.bridge == "bridge0"
    assert vm.vnc_port == "5900"
    assert vm.vnc_wait_connect.lower() == "false"
    assert vm.iso_path == "/path/to/linux_iso.iso"
    assert vm.bhyve_command is not None


def test_load_config_name_mismatch(vm_config_setup, tmp_path):
    vm_name = vm_config_setup.vm_name
    vm = AnlasserVM(vm_name, None)
    mismatched_path = Path(tmp_path, "othername.ini")
    mismatched_path.write_text(vm_config_setup.config_file_path.read_text(), encoding="utf-8")

    with pytest.raises(AnlasserVMError):
        vm.load_config(mismatched_path)


def test_load_config_missing_key(tmp_path):
    vm_name = "testvm1"
    vm = AnlasserVM(vm_name, None)
    config_path = Path(tmp_path, f"{vm_name}.ini")
    config_path.write_text("[VM]\nname = testvm1\n", encoding="utf-8")

    with pytest.raises(AnlasserVMError):
        vm.load_config(config_path)
