import json
import logging
import socket


def _get_socket(socket_path):
    ctl_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    ctl_sock.connect(socket_path)
    return ctl_sock


def _get_socket_data(ctl_sock, timeout):
    ctl_sock.settimeout(timeout)
    sock_file = ctl_sock.makefile("rb")
    raw = sock_file.readline(65536)
    if not raw:
        logging.info("No data left on socket, server has probably gone away.")
        return None
    if raw[-1:] != b"\n":
        logging.warning("Server message exceeded 64kb or missing terminator, discarded")
        return None
    # `repr()` prints newlines and other stuff as \n here, not as actual newlines etc.
    logging.debug(repr(f"raw server message: {raw}"))
    return raw


def communicate(socket_path, data, timeout=360):
    ctl_sock = _get_socket(socket_path)
    msg = json.dumps(data, ensure_ascii=False) + "\n"
    ctl_sock.sendall(msg.encode("UTF-8"))
    return _get_socket_data(ctl_sock, timeout)


def load_json_from_server_msg(raw_server_data):
    if raw_server_data.find(b"\n") == -1:
        logging.warning("Got message without terminator, discarded")
        return None
    try:
        parsed_data = json.loads(raw_server_data.decode("utf-8"))
    except (json.decoder.JSONDecodeError, TypeError):
        logging.warning("Unable to parse message as valid JSON, discarded")
        return None
    except UnicodeDecodeError:
        logging.warning("Unable to decode message into unicode, discarded")
        return None
    logging.debug(f"Server sent json: {parsed_data}")
    return parsed_data


# FIXME: we need a message schema that is shared between the client and server code so we can have proper validation.
# from jsonschema import validate
# def validate_server_message(parsed_data):


def parse_server_json(server_json=None):
    # This is a poor-mans effort at message parsing.
    # We'll improve once we are actually able to run some VMs.
    # Probably we should stuff the actual, function-specific
    # data inside a "data" field.
    if server_json is None:
        server_json = {}
    parsed_msg = dict()
    parsed_msg["result"] = server_json.get("result", None)
    if not parsed_msg["result"]:
        logging.error(f"Server didn't fill the 'result' field of the return message!")
        return False
    if parsed_msg["result"] == "success":
        parsed_msg["vm_state"] = server_json.get("vm_state", None)
        parsed_msg["vm_pid"] = server_json.get("vm_pid", None)
    elif parsed_msg["result"] == "failure":
        parsed_msg["error_type"] = server_json.get("error_type", None)
        parsed_msg["error_text"] = server_json.get("error_text", None)
    return parsed_msg
