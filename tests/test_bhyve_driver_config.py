import pytest

from pathlib import Path

from anlasser.bhyve_driver import AnlasserBhyveDriver
from anlasser.errors import AnlasserBhyveDriverError


def test_load_config_sets_minimal_bhyve_command(tmp_path):
    vm_name = "testvm1"
    vm = AnlasserBhyveDriver(vm_name)
    config_path = Path(tmp_path, f"{vm_name}.ini")
    uefi_vars_path = f"/tank/VMs/{vm_name}/BHYVE_UEFI_VARS.fd"
    config_path.write_text(
        "\n".join(
            [
                "[VM]",
                f"name = {vm_name}",
                "memory_mb = 1024",
                "cpu_sockets = 1",
                "cpu_cores = 2",
                "cpu_threads = 1",
                f"storage_path = /tank/VMs/{vm_name}/{vm_name}.img",
                f"uefi_vars_storage_path = {uefi_vars_path}",
                "tapdev = tap0",
                "bridge = bridge0",
                "vnc_port = 5900",
                "",
            ]
        ),
        encoding="utf-8",
    )

    vm.load_config(config_path)

    assert vm.bhyve_command is not None
    assert vm.bhyve_command[-1] == vm_name
    assert (
        f"bootrom,/usr/local/share/uefi-firmware/BHYVE_UEFI.fd,{uefi_vars_path}"
        in vm.bhyve_command
    )


def test_load_config_missing_key(tmp_path):
    vm_name = "testvm1"
    vm = AnlasserBhyveDriver(vm_name)
    config_path = Path(tmp_path, f"{vm_name}.ini")
    config_path.write_text("[VM]\nname = testvm1\n", encoding="utf-8")

    with pytest.raises(AnlasserBhyveDriverError):
        vm.load_config(config_path)
