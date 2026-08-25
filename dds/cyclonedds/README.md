# DDS host network tuning

The `10-*.conf` files in this directory are optional Linux `sysctl.d` configurations for DDS traffic.
They are repository examples and are not packaged, generated, copied, or applied by `robotics-dockers`.

Both files change host-wide kernel settings.
Review the resource and reliability tradeoffs in the linked ROS 2 guidance before applying them, especially on a shared machine.

## Configuration files

`10-cyclonedds.conf` raises the maximum receive socket buffer that Linux permits.
Cyclone DDS must also request an appropriate buffer through its own configuration; raising the kernel ceiling alone does not change the size requested by Cyclone DDS.

`10-ros2-cross-vendor-tuning.conf` reduces the lifetime of incomplete IPv4 fragments and raises the memory threshold for the IPv4 fragment reassembly queue.
These settings can reduce long receive stalls when UDP fragments are dropped, regardless of the DDS vendor.

## Install and verify

Install only the files required by the host:

```bash
sudo install -m 0644 dds/cyclonedds/10-cyclonedds.conf /etc/sysctl.d/
sudo install -m 0644 dds/cyclonedds/10-ros2-cross-vendor-tuning.conf /etc/sysctl.d/
```

Load each installed file explicitly so unrelated pending `sysctl.d` changes are not applied:

```bash
sudo sysctl --load /etc/sysctl.d/10-cyclonedds.conf
sudo sysctl --load /etc/sysctl.d/10-ros2-cross-vendor-tuning.conf
```

Read the effective values from the running kernel:

```bash
sysctl net.core.rmem_max
sysctl net.ipv4.ipfrag_time
sysctl net.ipv4.ipfrag_high_thresh
```

Linux processes `sysctl.d` files in lexicographic filename order.
A later file that assigns the same key overrides these values, so verify the effective settings after loading them and after rebooting.
