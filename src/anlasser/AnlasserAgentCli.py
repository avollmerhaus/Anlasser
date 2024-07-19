import argparse
import logging
import os
import signal
from pathlib import Path

from anlasser.AnlasserAgent import AnlasserAgent
from anlasser import __version__ as anlasser_version


def agent_cli():
    parser = argparse.ArgumentParser(description="AnlasserAgent")
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
    parser.add_argument(
        "--confdir",
        metavar="/usr/local/etc/anlasser",
        type=str,
        required=False,
        default="/usr/local/etc/anlasser",
        help="Directory with VM config files",
    )
    parser.add_argument(
        "--autostart",
        dest="autostart",
        action="store_true",
        default=False,
        help="Autostart all VMs that have the corresponding config flag set. Not implemented yet.",
    )
    parser.add_argument(
        "--debug",
        dest="debug",
        action="store_true",
        required=False,
        default=False,
        help="Activate debugging output",
    )

    cliargs = parser.parse_args()
    loglevel = logging.DEBUG if cliargs.debug else logging.INFO
    logging.basicConfig(level=loglevel, format="%(asctime)s %(message)s")

    vm_configs_dir = Path(cliargs.confdir).expanduser()
    if not vm_configs_dir.is_dir():
        logging.error(f"VM config directory {cliargs.confdir} not found, quit")
        return 4

    if cliargs.autostart:
        raise NotImplementedError

    agent = AnlasserAgent(vm_configs_dir=vm_configs_dir, socket_path=cliargs.socketpath)
    logging.info(
        f"Initialized AnlasserAgent, config dir {vm_configs_dir}, socket path {cliargs.socketpath}"
    )

    def exit_signal_handler(signal_number, _):
        sig_name = signal.Signals(signal_number).name
        logging.info(f"Got signal {sig_name}, triggering shutdown procedure")
        agent.shutdown_flag = True

    # SIGTERM and SIGINT should raise SystemExit,
    # the registered exit function inside AnlasserAgent is responsible for shutting down the VMs.
    signal.signal(signal.SIGTERM, exit_signal_handler)
    signal.signal(signal.SIGINT, exit_signal_handler)

    logging.info(os.environ["PATH"])
    return agent.run()


if __name__ == "__main__":
    raise SystemExit(agent_cli())
