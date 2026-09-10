// SPDX-License-Identifier: GPL-2.0-only
/*
 * Latency-first defaults for performance-oriented Android kernels.
 *
 * Android init remains free to override every value through sysctl.  This is
 * intentional: the profile supplies useful early defaults without creating a
 * second, hidden policy layer which fights userspace.
 */

#include <linux/dcache.h>
#include <linux/gfp.h>
#include <linux/init.h>
#include <linux/mm.h>
#include <linux/printk.h>
#include <linux/swap.h>

#define ANDROID_PERF_WMARK_SCALE_FACTOR	20
#define ANDROID_PERF_SWAPPINESS		160
#define ANDROID_PERF_VFS_CACHE_PRESSURE	150

static int __init android_performance_profile_init(void)
{
	int ret;

	/*
	 * Android normally swaps to compressed RAM.  Prefer compressing cold
	 * anonymous pages over reclaiming a foreground app's file working set,
	 * and wake kswapd early enough to avoid direct-reclaim stalls.
	 */
	ret = set_reclaim_params(ANDROID_PERF_WMARK_SCALE_FACTOR,
				 ANDROID_PERF_SWAPPINESS);
	if (ret)
		pr_warn("android-perf: failed to set reclaim defaults: %d\n", ret);

	/* Random zram reads do not benefit from block-device read-ahead. */
	WRITE_ONCE(page_cluster, 0);

	/* Reclaim cheap inode/dentry metadata before long-lived app memory. */
	WRITE_ONCE(sysctl_vfs_cache_pressure,
		   ANDROID_PERF_VFS_CACHE_PRESSURE);

	pr_info("android-perf: aggressive VM profile enabled\n");
	return 0;
}
late_initcall(android_performance_profile_init);
