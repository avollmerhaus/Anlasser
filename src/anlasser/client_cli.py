import argparse
import logging

import anlasser.client as Client
from anlasser import __version__ as anlasser_version
from anlasser.errors import (
    AnlasserCommandFailedError,
    AnlasserInvalidResponseError,
)


def _server_action(socket_path, data):
    server_response = Client.communicate(socket_path=socket_path, data=data)
    server_json = Client.load_json_from_server_msg(server_response)
    status = server_json["status"]
    if status < 200 or status > 299:
        body = server_json.get("body", {})
        error_message = body.get("error")
        raise AnlasserCommandFailedError(
            f"status={status}; error_message={error_message}"
        )
    logging.debug("_server_action returned parsed server_json")
    return server_json


def _set_vm_state(vm_name, target_state, socket_path):
    msg = {
        "action": "set_vm_state",
        "body": {"state": target_state, "vm_name": vm_name},
    }
    _server_action(socket_path=socket_path, data=msg)
    logging.info(f"VM {vm_name} set to state {target_state}")
    return 0


def _get_vm_state(vm_name, socket_path):
    msg = {"action": "get_vm_state", "body": {"vm_name": vm_name}}
    server_json = _server_action(socket_path=socket_path, data=msg)
    state = server_json["body"]["response"]["vm_state"]
    logging.info(f"VM {vm_name} is {state}")
    return 0


def _list_vms(socket_path):
    msg = {"action": "list_vms", "body": {}}
    server_json = _server_action(socket_path=socket_path, data=msg)
    result = server_json["body"]["response"]
    logging.info(result.get("vm_list", []))
    return 0


def client_cli():
    """
    This function implements the CLI interface itself.
    There are 3 possible actions at the moment:
     - list all running VMs
     - start/stop a VM
     - get state for a specific VM
    These functions are realized by calling into a specific helper function that
    builds a server message and uses _server_action to communicate with the server.
    We use the following return codes:
    - 0: successful 2xx response
    - 20: non-2xx response with valid protocol format
    - 30: malformed/undecodable/schema-invalid server response
    - 40: socket/transport failures (catch-all default)
    Keep in mind that argparse uses exit status 2 for errors in the cli arguments.
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

    returncode = 40
    try:
        if cliargs.set_state:
            returncode = _set_vm_state(
                vm_name=cliargs.vm,
                target_state=cliargs.set_state,
                socket_path=cliargs.socketpath,
            )
        elif cliargs.get_state:
            returncode = _get_vm_state(
                vm_name=cliargs.vm, socket_path=cliargs.socketpath
            )
        else:
            returncode = _list_vms(socket_path=cliargs.socketpath)
    except OSError as exc:
        logging.error(f"Unable to communicate with server socket: {exc}")
    except AnlasserInvalidResponseError as exc:
        logging.error(f"Server returned malformed response: {exc}")
        returncode = 30
    except AnlasserCommandFailedError as exc:
        logging.error("Server signalled command failure!")
        logging.error(str(exc))
        returncode = 20
    return returncode


if __name__ == "__main__":
    raise SystemExit(client_cli())
