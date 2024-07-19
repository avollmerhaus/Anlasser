import argparse
import logging
import shutil
import sys
from pathlib import Path

from anlasser import __version__ as anlasser_version
from anlasser.AnlasserMkVM import (create_sparse_file, create_zfs_dataset,
                                   write_base_config)


def mkvm_cli():
    parser = argparse.ArgumentParser(description="Anlasser-MkVM: Create a new VM")
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {anlasser_version}",
    )
    parser.add_argument(
        "--name",
        metavar="myvm",
        type=str,
        required=True,
        help="VM name",
    )
    parser.add_argument(
        "--parent-dataset",
        metavar="tank/my_anlasser_vms",
        type=str,
        required=True,
        help="ZFS dataset used as parent for VM dataset",
    )
    parser.add_argument(
        "--imgsize",
        metavar="42",
        type=int,
        required=True,
        help="Virtual disk size in gigabytes",
    )
    parser.add_argument(
        "--dataset-recordsize",
        metavar="64k",
        type=str,
        default="64k",
        help="ZFS dataset recordsize, the default 64k is a compromise. Tune to your workload.",
    )

    cliargs = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(message)s", stream=sys.stdout
    )

    logging.info(
        f"Creating ZFS dataset {cliargs.parent_dataset}/{cliargs.name}, recordsize {cliargs.dataset_recordsize}"
    )
    dataset_mountpath = create_zfs_dataset(
        cliargs.parent_dataset, cliargs.dataset_recordsize, cliargs.name
    )

    uefi_vars_path = Path(dataset_mountpath, "BHYVE_UEFI_VARS.fd")
    logging.info(f"Copying UEFI vars template to {uefi_vars_path}")
    shutil.copy2("/usr/local/share/uefi-firmware/BHYVE_UEFI_VARS.fd", uefi_vars_path)

    # Create sparse disk image file
    disk_image_path = f"{dataset_mountpath}/{cliargs.name}.img"
    logging.info(f"Creating {cliargs.imgsize}GB sparse file at {disk_image_path}")
    create_sparse_file(file_path=disk_image_path, size_gbs=cliargs.imgsize)

    config_file_path = f"/usr/local/etc/anlasser/{cliargs.name}.ini"
    logging.info(
        f"Writing a basic config template to {config_file_path}, please modify it to your liking"
    )
    write_base_config(cliargs.name, config_file_path, disk_image_path, uefi_vars_path)

    return 0


if __name__ == "__main__":
    raise SystemExit(mkvm_cli())
