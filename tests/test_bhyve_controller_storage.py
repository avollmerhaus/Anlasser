import pytest

from anlasser.bhyve_controller import AnlasserBhyveController
from anlasser.bhyve_controller_storage import build_disk_args
from anlasser.errors import AnlasserBhyveControllerError

from conftest import (
    _generate_config_disk_section,
    _write_config_file,
)


# Intention: verify a single disk is parsed correctly from config.
# Expected outcome: one disk with expected name and order.
def test_parse_single_disk(tmp_path):
    vm_name = "testvm1"
    vm = AnlasserBhyveController(vm_name)
    config_path = _write_config_file(
        tmp_path,
        vm_name,
        disks=[
            _generate_config_disk_section(
                "disk0", storage_path="/tank/VMs/testvm1/disk0.img", order=0
            )
        ],
    )

    vm.load_config(config_path)

    assert len(vm.disks) == 1
    assert vm.disks[0]["name"] == "disk0"
    assert vm.disks[0]["order"] == 0


# Intention: verify multiple disks are sorted by order regardless of config file order.
# Expected outcome: disks sorted ascending by order value.
def test_parse_multiple_disks_sorted_by_order(tmp_path):
    vm_name = "testvm1"
    vm = AnlasserBhyveController(vm_name)
    config_path = _write_config_file(
        tmp_path,
        vm_name,
        disks=[
            _generate_config_disk_section(
                "second", storage_path="/tank/VMs/testvm1/b.img", order=2
            ),
            _generate_config_disk_section(
                "first", storage_path="/tank/VMs/testvm1/a.img", order=0
            ),
        ],
    )

    vm.load_config(config_path)

    assert vm.disks[0]["order"] == 0
    assert vm.disks[1]["order"] == 2


# Intention: verify a config with no disk sections is valid.
# Expected outcome: vm.disks is empty.
def test_parse_zero_disks(tmp_path):
    vm_name = "testvm1"
    vm = AnlasserBhyveController(vm_name)
    config_path = _write_config_file(tmp_path, vm_name, disks=[])

    vm.load_config(config_path)

    assert vm.disks == []


# Intention: verify a disk section missing storage_path is rejected.
# Expected outcome: load_config raises AnlasserBhyveControllerError.
def test_parse_missing_storage_path(tmp_path):
    vm_name = "testvm1"
    vm = AnlasserBhyveController(vm_name)
    config_path = _write_config_file(
        tmp_path,
        vm_name,
        disks=[_generate_config_disk_section("disk0", order=0)],
    )

    with pytest.raises(AnlasserBhyveControllerError, match="storage_path"):
        vm.load_config(config_path)


# Intention: verify a disk section missing order is rejected.
# Expected outcome: load_config raises AnlasserBhyveControllerError.
def test_parse_missing_order(tmp_path):
    vm_name = "testvm1"
    vm = AnlasserBhyveController(vm_name)
    config_path = _write_config_file(
        tmp_path,
        vm_name,
        disks=[
            _generate_config_disk_section(
                "disk0", storage_path="/tank/VMs/testvm1/disk0.img"
            )
        ],
    )

    with pytest.raises(AnlasserBhyveControllerError, match="order"):
        vm.load_config(config_path)


# Intention: verify that exceeding the maximum disk count is rejected.
# Expected outcome: load_config raises AnlasserBhyveControllerError.
def test_parse_too_many_disks(tmp_path):
    vm_name = "testvm1"
    vm = AnlasserBhyveController(vm_name)
    config_path = _write_config_file(
        tmp_path,
        vm_name,
        disks=[
            _generate_config_disk_section(
                f"disk{i}", storage_path=f"/tank/VMs/testvm1/disk{i}.img", order=i
            )
            for i in range(4)
        ],
    )

    with pytest.raises(AnlasserBhyveControllerError, match="Too many disks"):
        vm.load_config(config_path)


# Intention: verify that duplicate disk order values are rejected.
# Expected outcome: load_config raises AnlasserBhyveControllerError.
def test_parse_duplicate_order(tmp_path):
    vm_name = "testvm1"
    vm = AnlasserBhyveController(vm_name)
    config_path = _write_config_file(
        tmp_path,
        vm_name,
        disks=[
            _generate_config_disk_section(
                "disk0", storage_path="/tank/VMs/testvm1/a.img", order=0
            ),
            _generate_config_disk_section(
                "disk1", storage_path="/tank/VMs/testvm1/b.img", order=0
            ),
        ],
    )

    with pytest.raises(AnlasserBhyveControllerError, match="Duplicate"):
        vm.load_config(config_path)


# Intention: verify that a disk order exceeding the valid range is rejected.
# Expected outcome: load_config raises AnlasserBhyveControllerError.
def test_parse_order_out_of_range(tmp_path):
    vm_name = "testvm1"
    vm = AnlasserBhyveController(vm_name)
    config_path = _write_config_file(
        tmp_path,
        vm_name,
        disks=[
            _generate_config_disk_section(
                "disk0", storage_path="/tank/VMs/testvm1/disk0.img", order=5
            )
        ],
    )

    with pytest.raises(AnlasserBhyveControllerError, match="order must be between"):
        vm.load_config(config_path)


# Intention: verify that a negative disk order is rejected.
# Expected outcome: load_config raises AnlasserBhyveControllerError.
def test_parse_negative_order(tmp_path):
    vm_name = "testvm1"
    vm = AnlasserBhyveController(vm_name)
    config_path = _write_config_file(
        tmp_path,
        vm_name,
        disks=[
            _generate_config_disk_section(
                "disk0", storage_path="/tank/VMs/testvm1/disk0.img", order=-1
            )
        ],
    )

    with pytest.raises(AnlasserBhyveControllerError, match="order must be between"):
        vm.load_config(config_path)


# Intention: verify a single disk produces the correct bhyve -s argument.
# Expected outcome: NVMe on slot 4 with sectsz=4096.
def test_build_disk_args_single():
    disks = [{"name": "disk0", "storage_path": "/tank/disk0.img", "order": 0}]
    args = build_disk_args(disks)
    assert args == ["-s", "4,nvme,/tank/disk0.img,sectsz=4096"]


# Intention: verify multiple disks produce sequential slot assignments.
# Expected outcome: slots 4, 5, 6 in order.
def test_build_disk_args_multiple():
    disks = [
        {"name": "disk0", "storage_path": "/tank/a.img", "order": 0},
        {"name": "disk1", "storage_path": "/tank/b.img", "order": 1},
        {"name": "disk2", "storage_path": "/tank/c.img", "order": 2},
    ]
    args = build_disk_args(disks)
    assert args == [
        "-s",
        "4,nvme,/tank/a.img,sectsz=4096",
        "-s",
        "5,nvme,/tank/b.img,sectsz=4096",
        "-s",
        "6,nvme,/tank/c.img,sectsz=4096",
    ]


# Intention: verify no disks produces no arguments.
# Expected outcome: empty list.
def test_build_disk_args_empty():
    assert build_disk_args([]) == []
