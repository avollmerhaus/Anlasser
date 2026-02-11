import argparse
import asyncio
import logging
from pathlib import Path

from anlasser.agent import AnlasserController
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

    # Maybe it would be a good idea to split the socket and client handling stuff
    # into a separate class.
    # The CLI could tie it together with the VM controller,
    # maybe the work queue could be created here and supplied to
    # the socket server class AND the VM controller?
    # It would free the controller class from all the client message parsing and handling.
    # How do we organize the async loop?
    # It needs to be moved in here to multiplex that shit?
    # Let's keep the CLI interface clean.
    # Move socket server stuff into the AnlasserSockServ class,
    # the VM subproc management into AnlasserBhyveDriverController,
    # and tie it all together via AnlasserAgent
    socket_path = Path(cliargs.socketpath).expanduser()
    try:
        socket_path.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        logging.error(f"Unable to remove existing socket at {socket_path}: {exc}")
        return 5

    controller = AnlasserController(
        vm_configs_dir=vm_configs_dir, socket_path=socket_path
    )
    logging.info(
        f"Initialized AnlasserController, config dir {vm_configs_dir}, socket path {cliargs.socketpath}"
    )
    return asyncio.run(controller.main())


if __name__ == "__main__":
    raise SystemExit(agent_cli())
