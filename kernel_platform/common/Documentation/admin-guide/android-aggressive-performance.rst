.. SPDX-License-Identifier: GPL-2.0-only

======================================
Android aggressive performance profile
======================================

Scope
=====

``CONFIG_ANDROID_AGGRESSIVE_PERFORMANCE`` is a latency-first profile for a
custom Android kernel.  It changes common-kernel policy only; it cannot remove
limits implemented in a Qualcomm/OEM vendor module, device tree, firmware,
PMIC, Android thermal HAL, ``lmkd``, ActivityManager, or an OEM process manager.

The profile:

* increases schedutil DVFS headroom from 25 percent to 50 percent;
* caps schedutil's initial rate limit at 500 microseconds;
* starts I/O-wakeup boosting at 25 percent of CPU capacity;
* selects a 1000 Hz scheduler tick and disables lazy RCU callbacks in the
  supplied arm64 GKI defconfig;
* defaults zram to LZ4 and enables optional zram writeback support; and
* initializes ``vm.swappiness=160``, ``vm.page-cluster=0``,
  ``vm.watermark_scale_factor=20``, and ``vm.vfs_cache_pressure=150``.

The VM values remain normal writable sysctls.  Android init can override them.

Thermal emergency-only mode
===========================

``CONFIG_ANDROID_THERMAL_EMERGENCY_ONLY`` suppresses generic ACTIVE and PASSIVE
trip notifications and bypasses the generic thermal governor.  HOT and CRITICAL
trips and the emergency shutdown path are preserved.  Hardware and firmware
limits remain outside the kernel thermal governor and can still reduce clocks.

This option is hazardous.  It can overheat the phone, shorten battery life,
force shutdowns, or damage hardware.  Keep a known-good boot image and use
external cooling.  Disable this option for daily use or any unattended device.

Background applications
=======================

The zram and reclaim defaults reduce direct-reclaim stalls and give cold
anonymous memory more opportunity to remain compressed.  They do not prevent a
userspace component from sending ``SIGKILL``.  Keeping selected applications
alive also requires device-specific changes to ``lmkd`` thresholds,
ActivityManager cached-process limits, battery restrictions, and any OPlus
process manager.  Those components are not present in Android Common Kernel.

Validation
==========

Compare sustained performance, frame-time percentiles, zram occupancy, PSI
memory stalls, temperature, and forced-shutdown behavior against a stock boot
image.  A short benchmark is not sufficient because the aggressive profile can
reach the thermal ceiling sooner than the stock policy.
