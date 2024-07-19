import atexit
import json
import logging
import os
import socket
import subprocess
from pathlib import Path
from time import sleep, time


class AnlasserAgent:

    def __init__(self, vm_configs_dir, socket_path):
        self._poll_rate = 0.2
        self._exit_code = 128
        self.shutdown_flag = False
        self._anlasser_watcher_procs = dict()

        self._vm_configs_dir = vm_configs_dir
        self._socket_path = Path(socket_path)
        self._client_socket = None
        if self._socket_path.exists():
            self._socket_path.unlink()

        old_umask = os.umask(0o077)
        self._server_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server_socket.bind(str(self._socket_path))
        os.umask(old_umask)

        atexit.register(self._shutdown)

    def _shutdown(self, timeout=330):
        self._server_socket.close()
        if self._socket_path.exists():
            self._socket_path.unlink()
        # FIXME: The shutdown timeout used here should not be hardcoded but somehow be defined on a per-VM basis!
        # Also note that it should be longer than the shutdown timeout in AnlasserVM to avoid killing that code.
        if len(self._anlasser_watcher_procs) > 0:
            self._parallel_vm_shutdown(
                shutdown_names=self._anlasser_watcher_procs.keys(), timeout=timeout
            )

    def _start_vm(self, vm_name):
        logging.info(f"Starting VM: {vm_name}")
        # Let's return True if we already have a VM by that name.
        # Otherwise, start one. In case we fail, the caller is expected to
        # catch whatever exception prevented us from doing so.
        # We want the error to bubble up to a place were something meaningful can be done with it.
        # Here, "something useful" is probably just "communicating the exact error to the user".
        vm_proc = self._anlasser_watcher_procs.get(vm_name)
        if vm_proc is not None:
            logging.info(f"VM {vm_name} should already be running, PID {vm_proc.pid}")
            return True
        else:
            # Ok, we don't know a VM by that name. Let's try to start one.
            vm_config_path = Path(self._vm_configs_dir, f"{vm_name}.ini")
            vm_watcher_proc_command = [
                "anlasser-vm",
                "--vmconfig",
                vm_config_path,
                # The "logfile" argument will start redirecting everything to that logfile once the logging system
                # is initialized.
                # But we should do something about error messages to stdout/err (like "invalid arguments").
                "--logfile",
                f"/var/log/anlasser/{vm_name}.log",
            ]
            # If the VM quits immediately, we'll know when we enter the main loop. That should be soon enough.
            # Also note we're starting the watcher in an extra session in order to prevent ^C and other terminal-level
            # stuff to reach the child processes.
            vm_watcher_proc = subprocess.Popen(
                vm_watcher_proc_command, start_new_session=True
            )
            self._anlasser_watcher_procs[vm_name] = vm_watcher_proc
            # I guess the definition of success here is "We didn't run into an exception"?
            return True

    def _stop_vm(self, vm_name, timeout=330):
        """
        Shut down a VM and return true when it is down.
        If the VM doesn't quit properly within the timeframe allotted using the timeout parameter,
        `_parallel_shutdown` will kill the VM process.
        Note that we will completely block while waiting for the VM to go down!
        Maybe we can improve that in the future. The best way to do that should use a holistic approach,
        like rewriting the whole codebase to use async.

        :param vm_name: name of the VM to shut down
        :param timeout: how long to wait for the VM to shut down properly
        :return: true if the VM had a proper shutdown, false if we had to kill it
        """
        if vm_name in self._anlasser_watcher_procs.keys():
            success = self._parallel_vm_shutdown(
                shutdown_names=[vm_name], timeout=timeout
            )
        else:
            success = True
            logging.info(
                f"VM {vm_name} seems to be down or unknown, nothing to do here"
            )
        return success

    def _process_client_set_vm_state_action(self, vm_name, vm_target_state):
        response_data = dict()
        response_success = False
        if vm_target_state == "up":
            response_success = self._start_vm(vm_name)
        elif vm_target_state == "down":
            response_success = self._stop_vm(vm_name)
        else:
            logging.warning(f"VM target state {vm_target_state} not in [up, down]")
            response_data["error_type"] = "client_message_malformed"
            response_data["error_text"] = "target_vm_state_unknown"
        # Are there any exceptions that we can reasonably catch here in order to inform the client (and then move on)?
        self._send_response(success=response_success, data=response_data)

    def _process_client_get_vm_state_action(self, vm_name):
        response_data = dict()
        vm_proc = self._anlasser_watcher_procs.get(vm_name)
        if vm_proc is not None:
            response_data["vm_state"] = "vm_up"
            response_data["vm_pid"] = vm_proc.pid
        else:
            # FIXME: return something different if there is no ini file for the VM in question!
            response_data["vm_state"] = "vm_down"
        self._send_response(success=True, data=response_data)

    def _process_client_list_vms(self):
        response_data = dict()
        response_data["vm_list"] = list()
        for name, proc in self._anlasser_watcher_procs.items():
            response_data["vm_list"].append(
                {"vm_name": name, "vm_controller_pid": proc.pid}
            )
        self._send_response(success=True, data=response_data)

    def _send_response(self, success, data):
        # FIXME: We should probably create a class just for messages.
        # That would give us the dearly-needed single location for message definitions and proper defaults.
        # It could even have a .json() function to dump the json, and the different messages could inherit
        # from a base message class.
        # The actual payload, like the VM state or the list of VMs, should probably also be wrapped in a "data" field.
        # Because it's content is so highly dependant on the function being called, verification of the higher level
        # should probably only check for the presence of the data field and the result field.
        type_success = type(success)
        # We need to catch success = None here!
        # *Someone* might have stuffed the return value of a function that didn't actually
        # return anything in there, leading to a "false-y" value!
        if type_success is not bool:
            raise ValueError(
                f"Arg success expected a bool, got {type_success} instead!"
            )
        data["result"] = "success" if success else "failure"
        logging.debug(f"Sending response to client: {data}")
        socket_msg = json.dumps(data) + "\n"
        try:
            self._client_socket.sendall(socket_msg.encode("utf-8"))
        except BrokenPipeError:
            logging.warning(
                "Client disconnected while we were sending data. Closing socket."
            )
            self._client_socket.close()
        return socket_msg

    def _process_client_data(self, raw_client_data):
        response_data = dict()
        client_json = self._parse_client_message(raw_client_data)
        if client_json:
            try:
                action = client_json["action"]
                if action == "set_vm_state":
                    vm_name = client_json["vm_name"]
                    target_state = client_json["vm_target_state"]
                    logging.info(
                        f"Processing set_vm_state message, target VM: {vm_name}, target state: {target_state}"
                    )
                    self._process_client_set_vm_state_action(vm_name, target_state)
                elif action == "get_vm_state":
                    vm_name = client_json["vm_name"]
                    logging.info(f"Processing get_vm_state action, target VM:{vm_name}")
                    self._process_client_get_vm_state_action(vm_name)
                elif action == "list_vms":
                    logging.info("Processing list_vms action")
                    self._process_client_list_vms()
                else:
                    logging.warning(f"Client requested unknown action {action}")
                    response_data["error_type"] = "client_message_malformed"
                    response_data["error_text"] = "requested_action_unknown"
                    response_data["error_extra_data"] = f"requested action: {action}"
                    self._send_response(success=False, data=response_data)
            except KeyError as e:
                logging.warning(f"Client message missing mandatory key {e}")
                response_data["error_type"] = "client_message_malformed"
                response_data["error_text"] = "missing_mandatory_key"
                response_data["error_extra_data"] = f"missing key: {e}"
                self._send_response(success=False, data=response_data)
        else:
            logging.warning(
                "client_json is None, nothing to process in _process_client_data"
            )

    def _parse_client_message(self, raw_client_data):
        parsed_data = None
        if raw_client_data.find(b"\n") == -1:
            logging.warning("Got message without terminator, discarded")
            response_data = {
                "error_type": "client_message_malformed",
                "error_text": "no_terminator",
            }
            self._send_response(success=False, data=response_data)
        else:
            try:
                parsed_data = json.loads(raw_client_data.decode("utf-8"))
            except json.decoder.JSONDecodeError:
                logging.warning("Unable to parse message as valid JSON, discarded")
                response_data = {
                    "error_type": "client_message_malformed",
                    "error_text": "json_decode_error",
                }
                self._send_response(success=False, data=response_data)
            except UnicodeDecodeError:
                logging.warning("Unable to decode message into unicode, discarded")
                response_data = {
                    "error_type": "client_message_malformed",
                    "error_text": "unicode_decode_error",
                }
                self._send_response(success=False, data=response_data)
        return parsed_data

    def _get_socket_data(self, timeout=2):
        self._client_socket.settimeout(timeout)
        client_msg = None
        data = None
        try:
            data = self._client_socket.recv(8192)
            if not data:
                logging.info("No data left on socket, client has probably gone away.")
            else:
                client_msg = data
        except TimeoutError:
            logging.warning(f"No data received for {timeout} seconds, timeout.")
        # `repr()` prints newlines and other stuff as \n here, not as actual newlines etc.
        logging.debug(repr(f"raw client message: {data}"))
        return client_msg

    def _reap_zombies(self, vm_dict):
        zombie_names = [
            name for name, proc in vm_dict.items() if proc.poll() is not None
        ]
        for name in zombie_names:
            vm_watcher_proc = self._anlasser_watcher_procs.pop(name)
            vm_watcher_proc.communicate()
            logging.info(f"VM {name} stopped, exit code {vm_watcher_proc.returncode}")
        return zombie_names

    def _parallel_vm_shutdown(self, shutdown_names, timeout=330):
        # FIXME: respect individual VM shutdown timeouts here!
        # And keep in mind that AnlasserVM uses 300 as a default shutdown timeout,
        # so we should wait a little longer than that.
        hitlist = {
            name: proc
            for name, proc in self._anlasser_watcher_procs.items()
            if name in shutdown_names
        }

        if len(hitlist) < 0:
            logging.error(
                f"_parallel_vm_shutdown invoked, but calculated hitlist is empty. shutdown_names is: {shutdown_names}, _anlasser_watcher_procs is {self._anlasser_watcher_procs.keys()} "
            )
            return False

        for vm_name, vm_proc in hitlist.items():
            logging.info(f"Sending SIGTERM to {vm_name}")
            vm_proc.terminate()

        deadline = time() + timeout
        while time() < deadline and len(hitlist) > 0:
            for name in hitlist.keys():
                logging.info(
                    f"VM {name}: {int(deadline - time())}s left before process will be killed"
                )
            sleep(1)

            stopped_vms = self._reap_zombies(hitlist)
            for name in stopped_vms:
                del hitlist[name]

        # Should there be anything left in the hitlist,
        # it means the timeout exceeded. We need to apply force now.
        for vm_name, vm_proc in hitlist.items():
            logging.warning(f"Sending SIGKILL to {vm_name}")
            vm_proc.kill()
        self._reap_zombies(hitlist)
        # This is another example of a bogus return value that will eventually get sent to the client.
        # If we had to kill VMs, did we truly succeed?
        # But if we return false, wouldn't the client rightfully assume that
        # the VMs are still alive even though we killed them?
        # I guess in the end, shutdown is more nuanced than "True" or "False". But let's address this later.
        return True

    def run(self):
        # This is the main entrypoint for consumers of this class
        # When invoked, we try accepting socket connections indefinitely.
        # We'll exit if we either run into an exception or the shutdown flag is set.
        # Presence of the shutdown flag is polled every 0.2 seconds as long as we're not in the middle of processing a
        # message from a client. See `self._poll_rate`.
        # If a client connects, we'll fetch data from the socket. If there is none, we assume
        # the connection to be dead and resume waiting for incoming connections / polling the flag.
        # If we get data, we'll try to process the data.
        # We start by parsing it into json, then validate the json, then try to execute if the message contains a valid
        # command.
        # Each step has the opportunity to break away and signal failure to the client, stopping any further processing
        # of the message.
        # While it's not optimal to have so many possible exits along the way, I had the impression that it's
        # better than collecting all possible error conditions in one convoluted function that would also
        # need access to all the additional information that is sometimes necessary to make that decision.
        # As far as client messages are concerned, the buck stops with the `_process_client_action_*` functions.
        # While there are "deeper" functions like `_start_vm`, `_stop_vm`, `_parallel_vm_shutdown`,
        # these will also be used by parts of the code that don't handle client messages.
        # Therefore, they have to be free of socket actions.
        # Because I want the ability to always tell the client WHY something failed, I made the decision to simply let
        # exceptions bubble up the stack instead of catching them in the low-level functions.
        # The exception should allows us to let the exact error wander up the stack to
        # a point where we can make a high-level decision to send it to the client,
        # make it visible in a meaningful way, or maybe even by simply allowing ourselves to crash.
        #
        # At the moment, each answer message is hand-crafted inside the respective function.
        # While an effort was made to keep it somewhat consistent, we'll probably need some kind of message class
        # in the future. We might even recycle that inside the client.
        #
        # Because the client is a one-shot cli utility, there is exactly one message and one answer
        # per connection for now.
        try:
            # > [backlog is] the number of unaccepted connections that the system will allow before refusing new ones.
            backlog = 1
            self._server_socket.listen(backlog)
            self._server_socket.settimeout(self._poll_rate)
            while self.shutdown_flag is False:
                self._client_socket = None
                try:
                    self._client_socket, address = self._server_socket.accept()
                    logging.info(f"Client connected via {self._socket_path}")
                    client_data = self._get_socket_data()
                    if client_data:
                        self._process_client_data(client_data)
                    else:
                        logging.info("Shutting down client connection")
                        self._client_socket.close()
                except socket.timeout:
                    self._reap_zombies(self._anlasser_watcher_procs)
        except Exception as e:
            logging.exception(e)
        finally:
            # If we made it this far, we consider the program run a success.
            # I speculated on whether I should set the `_exit_code` to something non-zero when
            # VMs failed to terminate and had to be killed, but decided against that. What would that mean?
            # A non-zero exit code usually signals a crash, in some circumstances even warranting a restart.
            # So let's quit with 0 even when VMs failed to go down in time.
            self._exit_code = 0
            return 0
