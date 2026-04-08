import asyncio
import logging


async def _create_tap(vm_name):
    """Create a tap device and set its description to the VM name.
    Returns the tap device name (e.g. "tap0").
    """
    proc = await asyncio.create_subprocess_exec(
        "ifconfig",
        "tap",
        "create",
        stdout=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"Failed to create tap device (rc={proc.returncode})")
    tapdev_name = stdout.decode("utf-8").strip()
    logging.info(f"VM {vm_name}: Created tap device {tapdev_name}")

    # Bring up and tag the tap device so `ifconfig` shows which VM it belongs to.
    # Setting "up" here means we don't need the net.link.tap.up_on_open sysctl.
    proc = await asyncio.create_subprocess_exec(
        "ifconfig",
        tapdev_name,
        "up",
        "description",
        f"anlasser-vm-{vm_name}",
        start_new_session=True,
    )
    rc = await proc.wait()
    if rc != 0:
        logging.warning(f"VM {vm_name}: Failed to configure {tapdev_name} (rc={rc})")

    return tapdev_name


async def add_tap(vm_name, bridge_name):
    """Create a tap device and add it to a bridge.
    Returns the tap device name.
    """
    tapdev_name = await _create_tap(vm_name)
    command = ["ifconfig", bridge_name, "addm", tapdev_name]
    logging.info(f"Running command: {command}")
    proc = await asyncio.create_subprocess_exec(
        *command,
        start_new_session=True,
    )
    rc = await proc.wait()
    if rc != 0:
        raise RuntimeError(f"ifconfig add failed for {tapdev_name} (rc={rc})")
    return tapdev_name


async def destroy_tap(tapdev_name):
    """Destroy a tap device."""
    command = ["ifconfig", tapdev_name, "destroy"]
    logging.info(f"Running command: {command}")
    proc = await asyncio.create_subprocess_exec(
        *command,
        start_new_session=True,
    )
    rc = await proc.wait()
    if rc != 0:
        raise RuntimeError(f"ifconfig destroy failed for {tapdev_name} (rc={rc})")
