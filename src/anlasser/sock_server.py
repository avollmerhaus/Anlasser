import os
import json
import asyncio
import logging
from pathlib import Path

from .messages import (
    parse_anlasser_request,
    validate_anlasser_response,
)
from .errors import (
    AnlasserError,
    AnlasserVMError,
    AnlasserInvalidActionError,
    AnlasserInvalidMessageError,
)


class AnlasserSockServer:

    def __init__(self, socket_path, workq):
        self._sock_path = Path(socket_path)
        self._workq = workq
        self._server = None

    async def serve(self):
        if self._sock_path.exists():
            self._sock_path.unlink()

        old_umask = os.umask(0o077)
        try:
            self._server = await asyncio.start_unix_server(
                self._handle_client_connection, path=str(self._sock_path)
            )
        finally:
            os.umask(old_umask)

        logging.info(f"Socket server listening on {self._sock_path}")
        try:
            await self._server.serve_forever()
        except asyncio.CancelledError:
            logging.info("Shutdown unix socket server")
            self._server.close()
            await self._server.wait_closed()
            raise

    async def _handle_client_connection(self, reader, writer):
        logging.info("Client connected")
        try:
            while True:
                try:
                    line = await reader.readline()
                except asyncio.LimitOverrunError:
                    logging.warning("Line exceeded 64kb; dropping client")
                    break
                if line == b"":
                    logging.debug("Client sent EOF")
                    break

                line = line.strip()
                if not line:
                    logging.warning("Client sent empty line, ignoring")
                    continue

                response_dict = await self._handle_client_msg(line)
                try:
                    response_json = json.dumps(response_dict)
                    response_bin = response_json.encode("utf-8") + b"\n"
                    writer.write(response_bin)
                    await writer.drain()
                except (ConnectionResetError, BrokenPipeError):
                    logging.warning("Client disconnected before ACK")
                    break

        finally:
            writer.close()
            await writer.wait_closed()

    async def _handle_client_msg(self, raw_message):

        # Workflow:
        # - response_future gets passed to the agent using workq
        # - agent uses set_result or set_exception on response_future (bringing uns back into this function)
        # - if set_exception was used on the future, the exception gets raised inside this function
        # - only AnlasserError and derived exceptions are to be set by the agent or handled here,
        #   all other exceptions are considered bugs that should simply crash the code.
        # - if set_result was used, we assume the action to be completed without error and the result to be the action result
        # - what that result might be depends on the action. For `set_vm_state``, it might be `None`. For `list_vms`, a list.
        # - generate a response
        # - run response through validator
        # - crash if it fails (that's a bug after all)
        response = {"success": False}
        try:
            parsed_client_msg = parse_anlasser_request(raw_message)

            loop = asyncio.get_running_loop()
            response_future = loop.create_future()

            await self._workq.put((parsed_client_msg, response_future))

            response["result"] = await response_future

        except (AnlasserInvalidMessageError, AnlasserInvalidActionError) as exc:
            logging.warning(f"Client action failed: {exc}; message={raw_message!r}")
            response["error"] = {"code": "invalid_request", "message": str(exc)}

        except AnlasserVMError as exc:
            logging.warning(f"Client action failed: {exc}")
            response["error"] = {"code": "vm_error", "message": str(exc)}

        except AnlasserError as exc:
            logging.warning(f"Client action failed: {exc}")
            response["error"] = {"code": "internal_error", "message": str(exc)}

        else:
            response["success"] = True

        validate_anlasser_response(response)
        return response
