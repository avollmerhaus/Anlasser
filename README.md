Anlasser
===

So far, this is _mostly_ a clunky subset of [vm-bhyve](https://github.com/churchers/vm-bhyve), but written in Python.  
To keep the code simple, this project currently makes the following more or less hardcoded assumptions:
- UEFI guests only
- Tap networking only. Devices are created and destroyed automatically, one per NIC section in the config
- Bridges must be pre-created from the outside
- Up to 3 NVMe disk images per VM (PCI slots 4-6)
- Disk image files must exist before starting a VM (use `anlasser-mkvm` or create them manually)
- Everyone wants to use NVMe for the storage device and virtio for the NIC
- A few other hardcoded Bhyve options
- Everyone wants a VNC Server on localhost for console access, to be used via `ssh -L` or something
- Backups and snapshots are to be handled from the outside using ZFS or other 3rd party software

All of these may or may not change if this project ever matures, some even made it into the FIXME list.  
Maybe VNC servers should be optional, plus optional serial ports for console access?  
Others may never change, I'm quite sure VM snapshots, backups and bridge device management will stay out of scope.  

## But why?
While there is already a lot of Bhyve management software out there,  
I felt a need to roll my own for a variety of reasons.  
  
First of all, it's a hobby project to take a break from configuration management and write some code again!  
Aside from that, most other tools didn't fit my bill 100%.  
The biggest issue was that my VMs rest on encrypted ZFS datasets and many tools operate under  
the assumption that the datasets are available during system boot.  
I also wanted to be as self-reliant as possible, without fear of having a building block of my private infrastructure
abandoned a year later.  
Many of these project seem to be a one-person shows (just like this one).  
Another consideration is that some of the other bhyve management solutions seem to presume that  
you want them to roll their own network bridges and whatnot,  
while I wanted to use pre-existing bridges that are already in use by my jails.  
So I didn't want any extra management layers. It's all configured via Ansible anyway.  
Even more considerations:
- The VM configuration and related tasks should be as Ansible-friendly as possible
- The software should be as lightweight as possible in order to keep maintenance burden down 

If you're looking for something more serious, maybe check out one of these repos:
- https://github.com/churchers/vm-bhyve
- https://github.com/DaVieS007/bhyve-webadmin
- https://github.com/yaroslav-gwit/HosterCore
- https://github.com/cbsd/cbsd
- https://github.com/yuichiro-naito/bmd

## Prerequisites
- Bridge devices specified in guest configs must already exist, configuring bridges on the host is out of scope here.
- Developed and tested on FreeBSD 15.0 and newer, YMMV for older versions.

## Example config
_test1.toml_:
```toml
[VM.general]
name = "test1"
memory_mb = 1024
cpu_sockets = 1
cpu_cores = 2
cpu_threads = 1
uefi_vars_storage_path = "/tank/VMs/test1/BHYVE_UEFI_VARS.fd"
shutdown_timeout = 90
# boot_iso_path = "/path/to/linux_iso.iso"

[VM.vnc]
# See /usr/share/bhyve/kbdlayout for a list of valid layouts
vnc_kbd_layout = "de_noacc"
vnc_port = 5900
# vnc_wait_connect = true

[VM.disks.disk0]
storage_path = "/tank/VMs/test1/test1_disk0.img"
order = 0

# Multiple disks are supported (max 3):
[VM.disks.disk1]
storage_path = "/tank/VMs/test1/test1_disk1.img"
order = 1

[VM.nics.nic0]
bridge = "bridge0"
mac = "02:00:00:00:02:01"

# Multiple NICs are supported:
[VM.nics.nic1]
bridge = "bridge1"
```

- Config files use TOML format and live in `/usr/local/etc/anlasser/`. The filename must match the VM name.
- Disk and NIC section names (e.g. `disk0`, `nic0`) are arbitrary identifiers. They are not used internally. Stick to simple ASCII names.
- Each disk section needs a `storage_path` and an `order` value (0, 1, or 2). The `order` determines PCI slot assignment. The boot disk should have the lowest order value.
- Maximum 3 NVMe disks per VM (mapped to PCI slots 4-6). This limit exists because UEFI guests expect disk devices in the slot 3-6 range, and slot 3 is reserved for ISO/CD. More disks can be supported in the future by reworking the PCI slot layout.
- `bridge` is required per NIC section.
- `mac` is optional per NIC. If omitted, Bhyve generates one automatically.
- Zero NIC sections means no networking.
- Tap devices are labelled `anlasser-vm-<name>` in `ifconfig` output for easy identification.
- If `boot_iso_path` is set, the ISO is attached as an AHCI CD-ROM device and a fresh UEFI vars file is copied to ensure the CD-ROM boots first. Remove the option after installation.

Be sure not to exceed the number of cores / threads that your CPU actually has.  
Note the number of threads is per-core and the number of cores is per-socket.  

### TSC clocksource stability
Linux guests on bhyve may log `Marking clocksource 'tsc' as unstable` and fall back to `hpet` (slower — each read causes a VM exit). This is a known false positive caused by vCPU scheduling jitter.  
Fixed in Linux >= 6.3. For older kernels (e.g. 5.14 / Rocky 9): add `tsc=nowatchdog` to boot parameters.  
This may be more likely to trigger with `fwcfg=qemu` enabled, possibly due to ACPI table delivery
differences (linker/loader patching vs static in-memory copy). Not confirmed.
  
We use flat files instead of zvols for the storage path.  
As per [vermaden](https://vermaden.wordpress.com/2023/08/18/freebsd-bhyve-virtualization/), raw files and nvme are faster than virtio and zvols!  

## Warning and notes on usage
You should not rely on this software for anything serious, obviously.  
Not only may it be full of horrible bugs and has barely seen any production usage, it also happily lets you shoot yourself in the foot.  
For example, it will currently merrily let you assign the same MAC address or backing storage device
to different VMs or NICs without complaint.  
While I will probably add some verification for VM configuration in the future, consider yourself warned.  
Watch your back if you're actually using this stuff.  

### Guest shutdown
Guests are expected to shut down immediately upon receipt of an ACPI shutdown signal from Bhyve.  
By default, `anlasser-agent` waits 90 seconds for a VM to shut down gracefully.  
If it doesn't, the Bhyve process gets killed.

### Boot order
UEFI firmware persists boot entries in the NVRAM vars file. Once entries exist, the stored
`BootOrder` takes precedence and newly attached CD-ROM devices get skipped.  
When `boot_iso_path` is set, Anlasser copies a fresh UEFI vars template before each start.
This forces firmware to re-enumerate devices, discovering the CD-ROM first
(removable media is enumerated before fixed disks). Remove `boot_iso_path` after installation.  
To change boot order manually, set `vnc_wait_connect = true` and press F2 during startup.

Note: bhyve's `bootindex` device option, which requires `fwcfg=qemu` on the bootrom line,
should theoretically override persisted boot order via edk2's `SetBootOrderFromQemu`.
In our testing (edk2-bhyve g202508, FreeBSD 15), this did not work — possibly due to
OFW-to-UEFI device path translation issues for AHCI-CD devices.
See `QemuBootOrderLib.c` and FreeBSD review D45768.

## How to use
FIXME: Write some actual command examples and stuff here

### How to run tests
First, instruct `poetry` to install the optional test group deps: `poetry install --with test`
Run pytest using `poetry run pytest`.  
That should look for all functions beginning with `test_` inside of all files that  
start with `test_` inside the `tests` folder.

### Release process

1. Update the version using Poetry:
   ```shell
   # For a patch release (bug fixes)
   poetry version patch

   # For a minor release (new features, backward compatible)
   poetry version minor

   # For a major release (breaking changes)
   poetry version major
   ```

2. Commit the version change:
   ```shell
   git add pyproject.toml
   git commit -m "Bump version to $(poetry version -s)"
   ```

3. Create and push the git tag:
   ```shell
   git tag -a $(poetry version -s) -m "Release $(poetry version -s)"
   git push --tags
   ```

## Future plans and important FIXMEs / bugs / missing features
- Implement a VM reset command. "bhyvectl --force-reset --vm test1"
- Communicate the VNC port in list_vms?
- Support more than one disk file in `anlasser-mkvm`
- Maybe we should port the networking to `vale`. That might yield better performance.
  VM configs could have a list of switches and multiple interfaces per switch. It seems vale could even be able to
  allow us to name interfaces according to their VM name? See https://gist.github.com/gonzopancho/f58516e98f6c8a5a3013
  - `3:0,virtio-net,vale0:vm1`, `-s 3:0,virtio-net,vale0:vm2`
  - How do we create the switch and add an uplink interface? `man valectl`, `man vale`
- `fwcfg=qemu` is enabled but `bootindex` does not work in practice (see "Boot order" above).
  The CPU core detection problem on Intel Atom C3558 (2024-08) was not reproducible on other
  hardware (2026-04, edk2-bhyve g202508).
- The VNC ports should be managed internally
- Serial console for the VMs. Currently commented out. Needs investigation into how guest serial output
  interacts with bhyve's own output, e.g. `com1,tcp=127.0.0.1:<port>` or logging to a file.
- At the moment, there is no autostarter for the VMs. While it's not a priority, it may still get implemented someday.
- Maybe a small local webserver with noVNC and start/stop buttons?
- Integrate [Black](https://black.readthedocs.io/en/stable/index.html) into some kind of pre-commit hook or something
- Windows support. See [FreeBSD Wiki on bhyve Windows support](https://wiki.freebsd.org/bhyve/Windows)
- NVMe tuning: bhyve supports `dsm=auto` (TRIM/deallocate, sensible for ZFS-backed storage), `maxq`/`qsz`/`ioslots`
  (queue depth and concurrency), `ser`/`eui64` (stable device identification in the guest), and `nocache`/`direct`
  (host caching bypass). These need benchmarking to determine optimal values. Maybe `sysbench`?
- `anlasser-mkvm --nosync` sets `sync=disabled` on the VM's ZFS dataset for faster I/O at the cost of
  durability. Intended for ephemeral VMs only — data may be lost on host crash or power failure.
- Support pci / nvme device passthrough
- Maybe we need a `--logfile` argument for `anlasser-agent`?
- Create a proper FreeBSD port. Maybe see https://github.com/psy0rz/zfs_autobackup/tree/master for how they do that.

## Debugging by hand
Running the agent: `poetry run anlasser-agent --socketpath /tmp/anlasser.sock --confdir /tmp`  
Running the client: ` poetry run anlasser-ctl --socketpath /tmp/anlasser.sock --set-state up --vm foo`
