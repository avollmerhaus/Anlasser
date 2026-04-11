import pytest

from anlasser.bhyve_controller import AnlasserBhyveController
from anlasser.errors import AnlasserBhyveControllerError

from conftest import _generate_config_nic_section, _write_config_file


# Intention: verify a single NIC section is parsed correctly.
# Expected outcome: one NIC with the expected bridge name.
def test_load_config_parses_nics(tmp_path):
    vm_name = "testvm1"
    vm = AnlasserBhyveController(vm_name)
    config_path = _write_config_file(tmp_path, vm_name)

    vm.load_config(config_path)

    assert len(vm.nics) == 1
    assert vm.nics[0]["bridge"] == "bridge0"


# Intention: verify a config with no NIC sections is valid.
# Expected outcome: vm.nics is empty.
def test_load_config_zero_nics(tmp_path):
    vm_name = "testvm1"
    vm = AnlasserBhyveController(vm_name)
    config_path = _write_config_file(tmp_path, vm_name, nics=[])

    vm.load_config(config_path)

    assert vm.nics == {}


# Intention: verify a NIC section missing the required bridge key is rejected.
# Expected outcome: load_config raises AnlasserBhyveControllerError.
def test_load_config_nic_missing_bridge(tmp_path):
    vm_name = "testvm1"
    vm = AnlasserBhyveController(vm_name)
    config_path = _write_config_file(
        tmp_path,
        vm_name,
        nics=[_generate_config_nic_section("nic0", mac="02:00:00:00:02:01")],
    )

    with pytest.raises(AnlasserBhyveControllerError):
        vm.load_config(config_path)
