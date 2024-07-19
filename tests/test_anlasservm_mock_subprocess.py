import atexit
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock, call

import pytest

from anlasser.AnlasserMkVM import write_base_config
from anlasser.AnlasserVM import AnlasserVM

# Keep in mind that the "mocker" fixture is provided by the "pytest-mock" package.


# This should probably be a pytest fixture.
# But I wanted to parametrize the vm_name, so we can have multiple ones in the future.
# While pytest fixtures have a facility for that,
# I found it to be overly complicated and I couldn't figure out how I could use the same variable for the VM name
# inside the decorator and the test function without defining it outside the functions, which I didn't like.
@contextmanager
def mk_configured_vm_dir(vm_name):
    configdir = tempfile.TemporaryDirectory()
    config_file_path = Path(configdir.name, f"{vm_name}.ini")
    write_base_config(
        name=vm_name,
        config_file_path=config_file_path,
        disk_image_path=f"/tank/VMs/{vm_name}/{vm_name}.img",
        uefi_vars_path=f"/tank/VMs/{vm_name}/BHYVE_UEFI_VARS.fd",
    )
    fake_vars_file = Path(configdir.name, "BHYVE_UEFI_VARS.fd")
    fake_vars_file.touch()
    try:
        yield Path(configdir.name)
    finally:
        configdir.cleanup()


def test_load_correct_config():
    # This test should verify that data structures match ini file contents.
    # As soon as anlasservm gains validation of config data,
    # we should test that by feeding incorrect data and checking for the correct failure mode.
    vm_name = "testvm1"
    with mk_configured_vm_dir(vm_name) as vm_config_dir:
        config_file_path = Path(vm_config_dir, f"{vm_name}.ini")
        vm = AnlasserVM()
        vm.load_config(config_file_path)

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


def test_wait_for_tap_device_creation(mocker):
    # The interface names used as mocked `ifconfig -l` output are lifted from a test server with one VM running.
    mocker.patch("subprocess.run", return_value=Mock(stdout="lo0 em0 bridge0 tap0\n"))
    vm = AnlasserVM()

    tap_device_detected = vm._wait_for_tap_device_creation(
        tapdev_name="tap0", timeout=0.1
    )

    assert tap_device_detected is True

    subprocess.run.assert_called_once_with(
        ["ifconfig", "-l"],
        capture_output=True,
        encoding="utf-8",
        start_new_session=True,
    )

    # Ensure TimeoutError is raised when the tap device doesn't pop up within the timeout
    with pytest.raises(TimeoutError):
        vm._wait_for_tap_device_creation(tapdev_name="tap1", timeout=0.1)


def test_add_tap_device_to_bridge(mocker):
    mocker.patch("subprocess.check_call")

    vm = AnlasserVM()
    # While I should test that the atexit function ran properly, I don't want to invest the time to figure out
    # how to do that now. So let's simply unregister it.
    atexit.unregister(vm._cleanup)

    vm._tap_operation(action="add", tapdev_name="tap0", bridge_name="bridge0")

    assert vm._network_setup_done
    subprocess.check_call.assert_called_once_with(
        ["ifconfig", "bridge0", "addm", "tap0"],
        **vm._subprocess_default_args,
    )


def test_vm_run_shutdown_flag_set(mocker):
    fake_bhyve_proc = Mock()

    # bhyve_proc.poll() needs to return `None` until `terminate` was called.
    def fake_poll():
        if call.terminate() in fake_bhyve_proc.method_calls:
            # A properly stopped bhyve proc should return 1 for the exit code,
            # because that's defined as "VM shutdown" by "man bhyve".
            fake_bhyve_proc.returncode = 1
            return 1
        if call.kill() in fake_bhyve_proc.method_calls:
            # 137 should be the exit code left behind when a bhyve proc gets killed.
            fake_bhyve_proc.returncode = 137
            return 137
        return None

    # Remember to always use a Mock object with a sideeffect here,
    # because we need to register the fact that `poll()` was called with `fake_bhyve_proc.method_calls`!
    fake_bhyve_proc.poll = Mock(side_effect=fake_poll)

    mocker.patch("subprocess.Popen", return_value=fake_bhyve_proc)

    def fake_tap_operation(action, tapdev_name, bridge_name=None):
        vm._network_setup_done = True

    vm_name = "testvm1"
    with mk_configured_vm_dir(vm_name) as vm_config_dir:
        config_file_path = Path(vm_config_dir, f"{vm_name}.ini")
        fake_uefi_vars_path = Path(vm_config_dir, "BHYVE_UEFI_VARS.fd")
        vm = AnlasserVM()
        # Not sure how to properly let the exit function do its thing within the context of tests, so skip that for now.
        # We can add a test for the vm._cleanup function separately.
        atexit.unregister(vm._cleanup)
        vm.load_config(config_file_path)
        vm.uefi_vars_storage_path = fake_uefi_vars_path
        # We need to quit the busy loop somehow, so let's simply set the shutdown flag before we even start.
        vm.shutdown_flag = True
        vm._wait_for_tap_device_creation = Mock()
        vm._tap_operation = Mock(side_effect=fake_tap_operation)
        fake_bhyve_exit_code = vm.run()

    # In a normal run, bhyve should return 1 ("vm powered off"), which maps to our exit code 0.
    assert fake_bhyve_exit_code == 0

    subprocess.Popen.assert_called_once_with(
        vm.bhyve_command,
        **vm._subprocess_default_args,
    )

    # The exact order and nature of calls is somewhat of an implementation detail, but it's better than nothing for now.
    expected_calls = [
        call.poll(),
        call.terminate(),
        call.wait(300),
        call.communicate(),
        call.poll(),
    ]
    fake_bhyve_proc.assert_has_calls(expected_calls, any_order=False)

    vm._tap_operation.assert_called_once_with("add", "tap0", "bridge0")

    # In order to further test the whole program, we could create a fake_bhyve_proc with an elaborate poll() method.
    # We know the polling frequency, that means we could increase a counter every time poll() is called.
    # If the counter reaches a certain limit, we can start to return something in response to poll() or
    # set the fake_bhyve_proc.returncode attribute.

    # Another interesting approach might be to simulate multiple bhyve runs.
    # It may be done like this:
    # bhyve_runs = (fake_bhyve_object_1, fake_bhyve_object_2, fake_bhyve_object_3)
    # Then patch anlasser.AnlasserVM._bhyve_proc_generator to return these, one after the other.
    # Use Mock(side_effect=bhyve_runs) to replace the proc generator, it will return items from the iterable one after
    # another.


# def test_socket_permissions():
#     # FIXME: move this to a different file
#     # FIXME: use temporary directory, create socket there, check socket permissions
#     pass
