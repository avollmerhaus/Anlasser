import argparse
import logging
import signal
from pathlib import Path
from shutil import which
from sys import stdout

from anlasser import __version__ as anlasser_version
from anlasser.AnlasserVM import AnlasserVM


def vm_cli():
    parser = argparse.ArgumentParser(description="AnlasserVM: Run a single bhyve VM")
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {anlasser_version}",
    )
    parser.add_argument(
        "--vmconfig",
        metavar="/usr/local/etc/anlasser/myvm.ini",
        type=str,
        required=True,
        help="Path to VM configuration file",
    )
    parser.add_argument(
        "--logfile",
        metavar="/var/log/myvm.log",
        nargs="?",
        type=argparse.FileType("a"),
        default=stdout,
        help="Path to logfile, defaults to stdout",
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
    logging.basicConfig(
        level=loglevel, format="%(asctime)s %(message)s", stream=cliargs.logfile
    )

    if not which("bhyve"):
        logging.error("bhyve executable not found in PATH, quit")
        return 127

    vm_config_file_path = Path(cliargs.vmconfig).expanduser()
    if not vm_config_file_path.is_file():
        logging.error(f"VM config file {vm_config_file_path} not found, quit")
        return 126

    anlasser_vm = AnlasserVM(logfile_object=cliargs.logfile)

    def exit_signal_handler(signal_number, _):
        sig_name = signal.Signals(signal_number).name
        logging.info(f"Got signal {sig_name}, triggering shutdown procedure")
        anlasser_vm.shutdown_flag = True

    signal.signal(signal.SIGTERM, exit_signal_handler)
    signal.signal(signal.SIGINT, exit_signal_handler)
    signal.signal(signal.SIGQUIT, exit_signal_handler)
    anlasser_vm.load_config(config_path=vm_config_file_path)
    return anlasser_vm.run()


if __name__ == "__main__":
    raise SystemExit(vm_cli())
