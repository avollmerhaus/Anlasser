import subprocess


# Keep in mind that we use this basic config for basic tests,
# so if you change something here you need to change the assertions in the tests as well.
def write_base_config(name, config_file_path, disk_image_path, uefi_vars_path):
    ini_content = f"""[VM]
name = {name}
memory_mb = 1024
cpu_sockets = 1
cpu_cores = 2
cpu_threads = 1
storage_path = {disk_image_path}
uefi_vars_storage_path = {uefi_vars_path}
tapdev = tap0
bridge = bridge0
mac = 02:00:00:00:02:01
# See /usr/share/bhyve/kbdlayout for a list of valid layouts
vnc_kbd_layout = de_noacc
vnc_port = 5900
# vnc_wait_connect = true
iso_path = /path/to/linux_iso.iso
"""
    with open(config_file_path, "w", encoding="utf-8") as config_file:
        config_file.write(ini_content)


def create_zfs_dataset(parent_dataset, recordsize, name):

    dataset = f"{parent_dataset}/{name}"

    subprocess.check_call(
        [
            "zfs",
            "create",
            "-o",
            f"recordsize={recordsize}",
            dataset,
        ]
    )

    mount_path_raw = subprocess.run(
        ["zfs", "get", "-H", "-o", "value", "mountpoint", dataset],
        capture_output=True,
        check=True,
    )
    return mount_path_raw.stdout.decode("utf8").strip()


def create_sparse_file(file_path, size_gbs):
    file_size = size_gbs * 1024**3
    with open(file_path, "wb") as imgfile:
        imgfile.seek(file_size - 1)
        imgfile.write(b"\0")
