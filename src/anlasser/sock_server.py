import os
import json
import asyncio
import logging
from pathlib import Path

from .messages import AnlasserResponse
from .errors import AnlasserError


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

                try:
                    client_msg = json.loads(line.decode())
                except json.JSONDecodeError as exc:
                    logging.warning(f"Client sent malformed JSON: {exc}")
                    continue
                # FIXME: instead of checking if we got a dict, we should
                # probably verify against a formal message spec.
                # messages.py ain't gonna cut it.
                if not isinstance(client_msg, dict):
                    logging.warning("Client sent non-object JSON, ignoring")
                    continue

                # FIXME: maybe some of the problems that result in abort before we land here
                # should generate proper `AnlasserResponse(success=False)` reactions.
                # Maybe generalize the try except for that.
                response = await self._handle_client_msg(client_msg)
                try:
                    # Should we verify that response is of type AnlasserResponse?
                    response_json = json.dumps(vars(response))
                    response_bin = response_json.encode("utf-8")
                    writer.write(response_bin)
                    await writer.drain()
                except (ConnectionResetError, BrokenPipeError):
                    logging.warning("Client disconnected before ACK")
                    break

        finally:
            writer.close()
            await writer.wait_closed()

    async def _handle_client_msg(self, client_msg):
        loop = asyncio.get_running_loop()
        response_future = loop.create_future()

        await self._workq.put((client_msg, response_future))
        try:
            response_payload = await response_future
            return AnlasserResponse(success=True, payload=response_payload)
        except AnlasserError as exc:
            logging.warning(f"Client action failed: {str(exc)}")
            return AnlasserResponse(success=False, payload=str(exc))
