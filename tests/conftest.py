from pathlib import Path


def _generate_config_general_section(vm_name):
    return "\n".join(
        [
            "[VM.general]",
            f'name = "{vm_name}"',
            "memory_mb = 1024",
            "cpu_sockets = 1",
            "cpu_cores = 2",
            "cpu_threads = 1",
            f'uefi_vars_storage_path = "/tank/VMs/{vm_name}/BHYVE_UEFI_VARS.fd"',
        ]
    )


def _generate_config_vnc_section():
    return "[VM.vnc]\nvnc_port = 5900\n"


def _generate_config_disk_section(disk_name, storage_path=None, order=None):
    lines = [f"[VM.disks.{disk_name}]"]
    if storage_path is not None:
        lines.append(f'storage_path = "{storage_path}"')
    if order is not None:
        lines.append(f"order = {order}")
    return "\n".join(lines)


def _generate_config_nic_section(nic_name, bridge=None, mac=None):
    lines = [f"[VM.nics.{nic_name}]"]
    if bridge is not None:
        lines.append(f'bridge = "{bridge}"')
    if mac is not None:
        lines.append(f'mac = "{mac}"')
    return "\n".join(lines)


def _write_config_file(tmp_path, vm_name, disks=None, nics=None, extra=""):
    config_path = Path(tmp_path, f"{vm_name}.toml")
    parts = [_generate_config_general_section(vm_name), _generate_config_vnc_section()]
    if disks is None:
        disks = [
            _generate_config_disk_section(
                "disk0", storage_path=f"/tank/VMs/{vm_name}/disk0.img", order=0
            )
        ]
    parts.extend(disks)
    if nics is None:
        nics = [_generate_config_nic_section("nic0", bridge="bridge0")]
    parts.extend(nics)
    if extra:
        parts.append(extra)
    config_path.write_text("\n".join(parts), encoding="utf-8")
    return config_path
