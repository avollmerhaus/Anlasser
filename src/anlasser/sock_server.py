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
    AnlasserBhyveControllerError,
    AnlasserInvalidActionError,
    AnlasserInvalidMessageError,
)


class AnlasserSockServer:

    def __init__(self, socket_path, handler):
        self._sock_path = Path(socket_path)
        self._handler = handler
        self._server = None

    async def serve(self):
        if self._sock_path.exists():
            raise RuntimeError(
                f"Socket file already exists at {self._sock_path}; "
                "another process might be running or the socket is stale. "
                "Remove it manually if you are sure it is safe."
            )

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
        finally:
            logging.info("Shutdown unix socket server")
            if self._server is not None:
                self._server.close()
                await self._server.wait_closed()
            if self._sock_path.exists():
                self._sock_path.unlink()

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
        # - request gets passed to the agent using a direct callback
        # - only AnlasserError and derived exceptions are to be raised by the agent or handled here,
        #   all other exceptions are considered bugs that should simply crash the code.
        # - if the callback returns, we assume the action to be completed without error and the result to be the action result
        # - result shape depends on the action (e.g. "ok" for set_vm_state, payload dicts for state/list operations)
        # - generate a response with HTTP-like status code and a body:
        #   {"status": 200, "body": {"response": {...}}}
        #   {"status": 400, "body": {"error": "some message"}}
        # - run response through validator
        # - crash if it fails (that's a bug after all)
        response = {
            "status": 500,
            "body": {
                "error": "Unhandled error",
            },
        }
        try:
            parsed_client_msg = parse_anlasser_request(raw_message)
            handler_result = await self._handler(parsed_client_msg)
            if handler_result is True:
                handler_result = "ok"
            response = {
                "status": 200,
                "body": {"response": handler_result},
            }

        except (AnlasserInvalidMessageError, AnlasserInvalidActionError) as exc:
            logging.warning(f"Client action failed: {exc}; message={raw_message!r}")
            response = {
                "status": 400,
                "body": {"error": str(exc)},
            }

        except AnlasserBhyveControllerError as exc:
            logging.warning(f"Client action failed: {exc}")
            response = {
                "status": 400,
                "body": {"error": str(exc)},
            }

        except AnlasserError as exc:
            logging.warning(f"Client action failed: {exc}")
            response = {
                "status": 500,
                "body": {"error": str(exc)},
            }

        validate_anlasser_response(response)
        return response
