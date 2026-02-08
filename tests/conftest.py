import pytest
import tempfile

from pathlib import Path
from dataclasses import dataclass

from anlasser.mkvm import write_base_config


@dataclass(frozen=True)
class VmConfigSetup:
    config_dir: Path
    vm_name: str
    config_file_path: Path


@pytest.fixture
def vm_config_setup():
    with tempfile.TemporaryDirectory() as configdir:
        vm_name = "testvm1"
        config_file_path = Path(configdir, f"{vm_name}.ini")
        write_base_config(
            name=vm_name,
            config_file_path=config_file_path,
            disk_image_path=f"/tank/VMs/{vm_name}/{vm_name}.img",
            uefi_vars_path=f"/tank/VMs/{vm_name}/BHYVE_UEFI_VARS.fd",
        )
        yield VmConfigSetup(
            vm_name=vm_name,
            config_dir=Path(configdir),
            config_file_path=config_file_path,
        )
