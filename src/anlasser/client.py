import json
import logging
import socket

from .errors import AnlasserInvalidResponseError
from .messages import validate_anlasser_response


def _get_socket(socket_path):
    ctl_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    ctl_sock.connect(socket_path)
    return ctl_sock


def _get_socket_data(ctl_sock, timeout):
    ctl_sock.settimeout(timeout)
    sock_file = ctl_sock.makefile("rb")
    raw = sock_file.readline(65536)
    if not raw:
        raise AnlasserInvalidResponseError(
            "No data left on socket, server has probably gone away"
        )
    if raw[-1:] != b"\n":
        raise AnlasserInvalidResponseError(
            "Server message exceeded 64kb or missing terminator"
        )
    # `repr()` prints newlines and other stuff as \n here, not as actual newlines etc.
    logging.debug(repr(f"raw server message: {raw}"))
    return raw


def communicate(socket_path, data, timeout=360):
    ctl_sock = _get_socket(socket_path)
    msg = json.dumps(data, ensure_ascii=False) + "\n"
    ctl_sock.sendall(msg.encode("UTF-8"))
    return _get_socket_data(ctl_sock, timeout)


def load_json_from_server_msg(raw_server_data):
    try:
        parsed_data = json.loads(raw_server_data.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise AnlasserInvalidResponseError(
            f"Unable to decode message into unicode: {exc}"
        ) from exc
    except (json.decoder.JSONDecodeError, TypeError) as exc:
        raise AnlasserInvalidResponseError(
            f"Unable to parse message as valid JSON: {exc}"
        ) from exc
    validate_anlasser_response(parsed_data)
    logging.debug(f"Server sent json: {parsed_data}")
    return parsed_data
