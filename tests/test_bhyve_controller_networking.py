import asyncio

import pytest

from anlasser import bhyve_controller_networking


class FakeProc:
    def __init__(self, stdout=b"", returncode=0):
        self._stdout = stdout
        self.returncode = returncode

    async def communicate(self):
        return self._stdout, b""

    async def wait(self):
        return self.returncode


def test_wait_for_tap_device_creation_found(monkeypatch):
    proc = FakeProc(stdout=b"lo0 em0 bridge0 tap0\n", returncode=0)

    async def fake_create_subprocess_exec(*args, **kwargs):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    assert asyncio.run(
        bhyve_controller_networking.wait_for_tap_device_creation("tap0", timeout=0.1)
    )


def test_wait_for_tap_device_creation_timeout(monkeypatch):
    proc = FakeProc(stdout=b"lo0 em0 bridge0\n", returncode=0)

    async def fake_create_subprocess_exec(*args, **kwargs):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    with pytest.raises(TimeoutError):
        asyncio.run(
            bhyve_controller_networking.wait_for_tap_device_creation("tap1", timeout=0.05)
        )


def test_tap_operation_add_success(monkeypatch):
    proc = FakeProc(returncode=0)

    async def fake_create_subprocess_exec(*args, **kwargs):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    assert asyncio.run(bhyve_controller_networking.tap_operation("add", "tap0", "bridge0"))


def test_tap_operation_destroy_failure(monkeypatch):
    proc = FakeProc(returncode=1)

    async def fake_create_subprocess_exec(*args, **kwargs):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    assert not asyncio.run(bhyve_controller_networking.tap_operation("destroy", "tap0"))
