Anlasser
===

So far, this is _mostly_ a clunky subset of [vm-bhyve](https://github.com/churchers/vm-bhyve), but written in Python.  
To keep the code simple, this project currently makes the following more or less hardcoded assumptions:
- UEFI guests only
- Every VM has exactly one tap device
- That tap device should be added to a bridge
- That bridge has been pre-created from the outside
- Every VM has exactly one backing storage path
- That device node or file has been pre-created from the outside
- Everyone wants to use NVMe for the storage device and virtio for the NIC
- A few other hardcoded Bhyve options
- Everyone wants a VNC Server on localhost for console access, to be used via `ssh -L` or something
- Backups and snapshots are to be handled from the outside using ZFS or other 3rd party software

All of these may or may not change if this project ever matures, some even made it into the FIXME list.
We will probably support multiple tap devices and storage devices per VM in the future.  
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
abandoned a year later. Many of these project seem to be a one-person shows (just like this one).  
Another consideration is that some of the other bhyve management solutions seem to presume that  
you want them to roll their own network bridges and whatnot, while I wanted to use pre-existing bridges that are already  
in use by my jails. So I didn't want any extra management layers. It's all configured via Ansible anyway.  
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
- Configure FreeBSD tap devices to be considered up on creation: `echo "net.link.tap.up_on_open=1" >> /etc/sysctl.conf`
- Developed and tested on FreeBSD 14.1 and newer, YMMV for older versions.

## Example config
_test1.ini_:
```ini
[VM]
name = test1
memory_mb = 1024
cpu_sockets = 1
cpu_cores = 2
cpu_threads = 1
storage_path = /tank/VMs/test1/test1.img
uefi_vars_storage_path = /tank/VMs/test1/BHYVE_UEFI_VARS.fd
tapdev = tap0
bridge = bridge0
mac = 02:00:00:00:02:01
# See /usr/share/bhyve/kbdlayout for a list of valid layouts
vnc_kbd_layout = de_noacc
vnc_port = 5900
# vnc_wait_connect = true
# iso_path = /path/to/linux_iso.iso
```

If `iso_path` is configured, we'll boot from the iso file.  
If `mac` is configured, it will override mac address auto-generation.

Be sure to match the number of sockets and threads to your CPU, or at least don't exceed the number of cores / threads  
that your CPU actually has.  
The Linux kernel inside the guest might otherwise start using `hpet` instead of `tsc` as a clock source.  
That may lead to degraded performance. Look for `clocksource` entries in `dmesg`.  
Note the number of threads is per-core and the number of cores is per-socket.  
  
We use flat files instead if zvols for the storage path.  
As per [vermaden](https://vermaden.wordpress.com/2023/08/18/freebsd-bhyve-virtualization/), raw files and nvme are faster than virtio and zvols!  

## Warning and notes on usage
You should not rely on this software for anything serious, obviously.  
Not only may it be full of horrible bugs and is poorly tested, it also happily lets you shoot yourself in the foot.  
For example, it will currently merrily let you assign the same MAC address or backing storage device  
to different VMs or other stupid stuff without complaint.  
While I will probably add some verification for VM configuration in the future, consider yourself warned.  
Watch your back if you're actually using this stuff.  

### Guest shutdown
Guests are expected to shut down immediately upon receipt of an ACPI shutdown signal from Bhyve.  
By default, `anlasseragent` waits 300 seconds for a VM to shut down gracefully.  
If it doesn't, the Bhyve process gets killed.

### Re-installing guest OSes
At the moment, adding an ISO file to a VM doesn't change the boot order.  
This is not a big deal if there is no OS installed inside the VM,  
but makes it hard to re-install the OS because he VM will continue to boot from it's normal UEFI vars entry.  
Bhyve supports a bootindex order, but the TianoCore firmware inside the guest may ignore that.  
The simplest way is probably to simply replace the UEFI vars file for the VM with a fresh one.  
That should lead to the VM defaulting to a boot from the ISO file.  
If you want to change the boot order manually, set `vnc_wait_connect = True` in the VM config and mash F2 during  
VM startup.

## How to use
FIXME: Write some actual command examples and stuff here

### How to run tests
Run pytest using `poetry run pytest`.  
That should look for all functions beginning with `test_` inside of all files that  
start with `test_` inside the `tests` folder.

## Future plans and important FIXMEs / bugs / missing features
- Implement a VM reset command. "bhyvectl --force-reset --vm test1"
- Communicate the VNC port in list_vms?
- Support more than one disk file in `AnlasserMkVMCli`
- We should support multiple disks per VM
- We should support multiple network interfaces per VM. While we're at it, maybe we should port the whole networking  
  stuff to `vale`. That might greatly simplify things and yield better performance.  
  VM configs could have a list of switches and multiple interfaces per switch. It seems vale could even be able to  
  allow us to name interfaces according to their VM name? See https://gist.github.com/gonzopancho/f58516e98f6c8a5a3013
  - `3:0,virtio-net,vale0:vm1`, `-s 3:0,virtio-net,vale0:vm2`
  - How do we create the switch and add an uplink interface? `man valectl`, `man vale`
- When changing tap device names, you may need to stop all VMs to ensure proper tap device assignment.  
  We should manage tap device enumeration internally. Device names could be generated by `anlasseragent` and  
  passed to `anlasservm` via cli. Or maybe this will all go away when we switch to `vale`.
- The VNC ports should be managed internally
- JSON messages exchanged by agent and client are generated from different functions,  
  with no centralized definition or specification for the protocol
- Serial console for the VMs
- At the moment, there is no autostarter for the VMs. While it's not a priority, it may still get implemented someday.
- Maybe a small local webserver with noVNC and start/stop buttons?
- Extend testing to test `anlasseragent` and `anlasserctl` as well
- Integrate [Black](https://black.readthedocs.io/en/stable/index.html) into some kind of pre-commit hook or something
- Windows support. See [FreeBSD Wiki on bhyve Windows support](https://wiki.freebsd.org/bhyve/Windows)
- At the moment, `anlasseragent` is blocked when processing a VM shutdown or something. The whole architecture of the  
  program needs to be redesigned, probably using async/await or threads
- Extend testing to check correct responses for deliberately false inputs, like incorrect configs,  
  configs which present duplicate VM names or incorrect cli flags
- `anlasserctl` output should be nicely formatted instead of timestamped raw json
- Do we need the "nocache" and/or "direct" options for our nvme storage? Or adapt number of queues to our HDD count?  
  Use some cross-platform benchmarks to compare host, guests and options. Maybe `sysbench`?
- Support pci / nvme device passthrough
- Maybe we need a `--logfile` argument for `AnlasserAgentCli`?
- The shutdown timeout option from the VM config file should be respected by `AnlasserAgent`
- Maybe create a class that defines some common methods and attributes like `shutdown_flag`, `_exit_code` and inherit
  from that in `AnlasserVM` and `AnlasserAgent`.
- Create a proper FreeBSD port. Maybe see https://github.com/psy0rz/zfs_autobackup/tree/master for how they do that.
