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


def test_add_tap_creates_and_bridges(monkeypatch):
    calls = []

    async def fake_create_subprocess_exec(*args, **kwargs):
        calls.append(args)
        if args[:3] == ("ifconfig", "tap", "create"):
            return FakeProc(stdout=b"tap3\n", returncode=0)
        # description and bridge-add calls
        return FakeProc(returncode=0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    result = asyncio.run(bhyve_controller_networking.add_tap("testvm1", "bridge0"))
    assert result == "tap3"
    assert calls[0] == ("ifconfig", "tap", "create")
    assert calls[1] == ("ifconfig", "tap3", "up", "description", "anlasser-vm-testvm1")
    assert calls[2] == ("ifconfig", "bridge0", "addm", "tap3")


def test_add_tap_fails_on_tap_creation(monkeypatch):
    async def fake_create_subprocess_exec(*args, **kwargs):
        return FakeProc(returncode=1)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    with pytest.raises(RuntimeError):
        asyncio.run(bhyve_controller_networking.add_tap("testvm1", "bridge0"))


def test_add_tap_fails_on_bridge_add(monkeypatch):
    calls = []

    async def fake_create_subprocess_exec(*args, **kwargs):
        calls.append(args)
        if args[:3] == ("ifconfig", "tap", "create"):
            return FakeProc(stdout=b"tap0\n", returncode=0)
        if "addm" in args:
            return FakeProc(returncode=1)
        return FakeProc(returncode=0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    with pytest.raises(RuntimeError):
        asyncio.run(bhyve_controller_networking.add_tap("testvm1", "bridge0"))


def test_destroy_tap_success(monkeypatch):
    async def fake_create_subprocess_exec(*args, **kwargs):
        return FakeProc(returncode=0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    asyncio.run(bhyve_controller_networking.destroy_tap("tap0"))


def test_destroy_tap_failure(monkeypatch):
    async def fake_create_subprocess_exec(*args, **kwargs):
        return FakeProc(returncode=1)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    with pytest.raises(RuntimeError):
        asyncio.run(bhyve_controller_networking.destroy_tap("tap0"))
