import argparse
import logging
from pathlib import Path

import anlasser.AnlasserClient as Client
from anlasser import __version__ as anlasser_version


def _get_server_data(socket_path, data):
    server_response = Client.communicate(socket_path=socket_path, data=data)
    server_json = Client.load_json_from_server_msg(server_response)
    if not server_json.get("result"):
        logging.error(
            f"Server didn't fill the 'result' field of the return message! Message invalid!"
        )
        return "server_data_malformed"
    if server_json["result"] == "failure":
        error_type = server_json["error_type"]
        error_text = server_json["error_text"]
        extra_data = server_json.get("error_extra_data", "(No extra data)")
        logging.error("Server signalled command failure!")
        logging.error(f"error_type: {error_type}")
        logging.error(f"error_text: {error_text}")
        logging.error(f"error_extra_data: {extra_data}")
        return "server_command_failed"
    logging.debug("_get_server_data returned parsed server_json")
    return server_json


def _set_vm_state(vm_name, target_state, socket_path):
    msg = dict()
    msg["action"] = "set_vm_state"
    msg["vm_target_state"] = target_state
    msg["vm_name"] = vm_name
    server_json = _get_server_data(socket_path=socket_path, data=msg)
    if server_json in ["server_command_failed", "server_data_malformed"]:
        return 101
    logging.info(f"VM {vm_name} set to state {target_state}")
    return 0


def _get_vm_state(vm_name, socket_path):
    msg = dict()
    msg["action"] = "get_vm_state"
    server_json = _get_server_data(socket_path=socket_path, data=msg)
    if server_json in ["server_command_failed", "server_data_malformed"]:
        return 101
    state = server_json["vm_state"]
    logging.info(f"VM {vm_name} is {state}")
    return 0


def _list_vms(socket_path):
    msg = dict()
    msg["action"] = "list_vms"
    server_json = _get_server_data(socket_path=socket_path, data=msg)
    if server_json in ["server_command_failed", "server_data_malformed"]:
        return 101
    logging.info(server_json["vm_list"])
    return 0


def client_cli():
    """
    This function implements the CLI interface itself.
    There are 3 possible actions at the moment:
     - list all running VMs
     - start/stop a VM
     - get state for a specific VM
    These functions are realized by calling into a specific helper function that
    builds a server message and uses _get_server_data to communicate with the server.
    The only JSON field that is mandatory at the moment is the "success" field,
    it's absence is handled by the _get_server_data function by returning a "server_data_malformed"
    string.
    The _get_server_data function also handles the case when the server signals failure by printing
    the relevant info and returning "server_command_failed".
    Helper functions should check for these strings and act accordingly.
    More specific failure modes should be handled by the specific functions.
    These functions also inform the user about the result of their action (if it didn't run into
    the error modes in _get_server_data).
    The helper functions should return an exit code that is then surfaced all the way up to the
    calling shell.
    Malfunction of the server should always use exit codes > 100.
    Malfunction of the client should always use exit codes < 100.
    Keep in mind that argparse uses exit status 2 for errors in the cli arguments,
    so don't use it for other stuff!

    In the future, we'll need to drastically improve the control flow here,
    starting with proper message parsing.
    We should probably start with a messages class with json serialization / deserialization features.
    But let's get some VMs off the ground first.
    """
    parser = argparse.ArgumentParser(
        description="AnlasserCtl: CLI Interface for Anlasser"
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {anlasser_version}",
    )
    parser.add_argument(
        "--socketpath",
        metavar="/var/run/anlasser.sock",
        type=str,
        required=False,
        default="/var/run/anlasser.sock",
        help="Path to the agent socket",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--set-state",
        dest="set_state",
        type=str,
        metavar="up|down",
        choices=("up", "down"),
    )
    group.add_argument("--get-state", dest="get_state", action="store_true")
    group.add_argument("--list-vms", dest="list_vms", action="store_true")

    parser.add_argument(
        "--vm", metavar="myvm", type=str, required=False, help="The VM to work on"
    )
    parser.add_argument(
        "--debug",
        dest="debug",
        action="store_true",
        required=False,
        default=False,
        help="Activate debugging output",
    )
    # FIXME: We should probably take care of the "start/stop all VMs" use case here.
    # That might take a multitude of forms:
    # - Not specifying a VM name might default to ALL VMs. A bad idea, what if the option wasn't given by mistake.
    # - Having a special "ALL" VM name? Kinda ugly, why would a VM name be reserved.
    # - Having special options like "--all-vms"
    # Side note: starting "all" VMs should probably start only VMs that have the special autostart property set to true.

    cliargs = parser.parse_args()

    if (cliargs.set_state or cliargs.get_state) and cliargs.vm is None:
        parser.error("Setting or getting the VM state needs a VM name")

    loglevel = logging.DEBUG if cliargs.debug else logging.INFO
    logging.basicConfig(level=loglevel, format="%(asctime)s %(message)s")

    if not Path(cliargs.socketpath).exists():
        logging.error(f"No socket at {cliargs.socketpath}, abort")
        return 1

    # Keep in mind that argparse uses exit status 2 for errors in the cli arguments, so don't use it for other stuff
    if cliargs.set_state:
        returncode = _set_vm_state(
            vm_name=cliargs.vm,
            target_state=cliargs.set_state,
            socket_path=cliargs.socketpath,
        )
    elif cliargs.get_state:
        returncode = _get_vm_state(vm_name=cliargs.vm, socket_path=cliargs.socketpath)
    elif cliargs.list_vms:
        returncode = _list_vms(socket_path=cliargs.socketpath)
    else:
        logging.error("No known cli action selected. This code should be unreachable")
        returncode = 9
    return returncode


if __name__ == "__main__":
    raise SystemExit(client_cli())
