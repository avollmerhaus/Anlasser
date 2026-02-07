import asyncio
import logging


async def wait_for_tap_device_creation(tapdev_name, timeout=5.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    logging.info(f"Waiting {timeout}s for tap device {tapdev_name} to appear")
    while loop.time() < deadline:
        proc = await asyncio.create_subprocess_exec(
            "ifconfig",
            "-l",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode == 0:
            available_interfaces = stdout.decode("utf-8").split()
            if tapdev_name in available_interfaces:
                logging.info(f"{tapdev_name}: tap device has been created")
                return True
        await asyncio.sleep(0.2)
    raise TimeoutError(f"Timeout waiting for tap device {tapdev_name} to appear")


async def tap_operation(action, tapdev_name, bridge_name=None):
    """
    We should add capability for multiple tap devices here.
    Maybe simply loop through them.

    :param action: "add" or "destroy"
    """
    ifconfig_commands = {
        "add": ["ifconfig", bridge_name, "addm", tapdev_name],
        "destroy": ["ifconfig", tapdev_name, "destroy"],
    }

    command = ifconfig_commands[action]
    logging.info(f"Running command: {command}")
    proc = await asyncio.create_subprocess_exec(
        *command,
        start_new_session=True,
    )
    rc = await proc.wait()
    if rc > 0:
        # Originally I tried to shut the VM down in case I was unable to add a tap device to a bridge,
        # but that just complicates things.
        # Sending SIGTERM to bhyve mere seconds after starting while the VM might still be booting probably
        # won't do us any good. So log the error and let the user deal with the problem,
        # they can shut the VM down if so desired.
        logging.error(f"Error running ifconfig: {rc}")
        return False
    return True
