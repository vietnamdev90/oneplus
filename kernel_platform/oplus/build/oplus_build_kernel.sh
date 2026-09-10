#!/bin/bash

set -o pipefail

source kernel_platform/oplus/build/oplus_setup.sh $1 $2
init_build_environment

function flatten_vendor_ramdisk_modules() {
    local compressed_ramdisk=$1
    local tools_bin="${TOPDIR}/kernel_platform/prebuilts/kernel-build-tools/linux_musl-x86/bin"
    local ramdisk_root=$2
    local modules_root
    local flat_modules="${ramdisk_root}/lib/modules.flatten"
    local module_count unique_count

    mkdir -p "${ramdisk_root}"
    "${tools_bin}/lz4" -dc "${compressed_ramdisk}" | \
        (cd "${ramdisk_root}" && cpio -idm --no-absolute-filenames >/dev/null 2>&1)

    modules_root=$(find "${ramdisk_root}/lib/modules" -mindepth 1 -maxdepth 1 -type d -print -quit)
    if [[ -z "${modules_root}" ]]; then
        echo "ERROR: vendor ramdisk has no versioned lib/modules directory" >&2
        return 1
    fi

    mkdir -p "${flat_modules}"
    find "${modules_root}" -type f -name '*.ko' -exec cp '{}' "${flat_modules}/" ';'
    module_count=$(find "${modules_root}" -type f -name '*.ko' | wc -l)
    unique_count=$(find "${flat_modules}" -type f -name '*.ko' | wc -l)
    if [[ "${module_count}" -ne "${unique_count}" ]]; then
        echo "ERROR: duplicate module basenames prevent flattening vendor_boot (${module_count} paths, ${unique_count} unique)" >&2
        return 1
    fi

    for metadata in modules.alias modules.blocklist modules.dep modules.load modules.options modules.softdep; do
        if [[ -f "${modules_root}/${metadata}" ]]; then
            cp "${modules_root}/${metadata}" "${flat_modules}/${metadata}"
        fi
    done
    if [[ -f "${flat_modules}/modules.dep" ]]; then
        sed -Ei 's#(kernel|extra)/[^:[:space:]]*/([^/:[:space:]]+\.ko)#\2#g' "${flat_modules}/modules.dep"
    fi
    if [[ -f "${flat_modules}/modules.load" ]]; then
        sed -Ei 's#.*/##' "${flat_modules}/modules.load"
    fi

    rm -rf -- "${ramdisk_root}/lib/modules"
    mv "${flat_modules}" "${ramdisk_root}/lib/modules"
    "${tools_bin}/mkbootfs" "${ramdisk_root}" | \
        "${tools_bin}/lz4" -c -l -12 --favor-decSpeed > "${compressed_ramdisk}"
    echo "vendor_boot ramdisk flattened: ${unique_count} modules"
}

function repack_vendor_boot_with_merged_dtb() {
    local image_dist="${TOPDIR}/kernel_platform/out/msm-kernel-${variants_platform}-${variants_type}/dist"
    local merged_dtb="${TOPDIR}/device/qcom/${variants_platform}-kernel/dtbs/dtb.img"
    local vendor_boot="${image_dist}/vendor_boot.img"
    local unpack_tool="${TOPDIR}/kernel_platform/tools/mkbootimg/unpack_bootimg.py"
    local mkbootimg_tool="${TOPDIR}/kernel_platform/tools/mkbootimg/mkbootimg.py"
    local avbtool="${TOPDIR}/kernel_platform/prebuilts/kernel-build-tools/linux_musl-x86/bin/avbtool"
    local partition_size=$((0x6000000))
    local repack_tmp
    local boot_args

    for required_file in "${vendor_boot}" "${merged_dtb}" "${unpack_tool}" "${mkbootimg_tool}" "${avbtool}"; do
        if [[ ! -f "${required_file}" ]]; then
            echo "ERROR: cannot repack vendor_boot; missing ${required_file}" >&2
            return 1
        fi
    done

    repack_tmp=$(mktemp -d)
    boot_args=$(python3 "${unpack_tool}" \
        --boot_img "${vendor_boot}" \
        --out "${repack_tmp}/unpacked" \
        --format=mkbootimg)

    cp "${merged_dtb}" "${repack_tmp}/unpacked/dtb"
    flatten_vendor_ramdisk_modules \
        "${repack_tmp}/unpacked/vendor_ramdisk00" \
        "${repack_tmp}/ramdisk"
    bash -c "python3 '${mkbootimg_tool}' ${boot_args} --vendor_boot '${repack_tmp}/vendor_boot.img'"
    "${avbtool}" add_hash_footer \
        --image "${repack_tmp}/vendor_boot.img" \
        --algorithm NONE \
        --partition_size "${partition_size}" \
        --partition_name vendor_boot

    install -m 0644 "${repack_tmp}/vendor_boot.img" "${vendor_boot}"
    if [[ -d "${TOPDIR}/out/dist" ]]; then
        install -m 0644 "${vendor_boot}" "${TOPDIR}/out/dist/vendor_boot.img"
    fi
    rm -rf -- "${repack_tmp}"
}

function build_kernel_cmd() {
    build_start_time

    if [ "${variants_type}" == "consolidate" ]; then
       if ! LTO=thin RECOMPILE_KERNEL=${RECOMPILE_KERNEL:-1} ./kernel_platform/build/android/prepare_vendor.sh $variants_platform $variants_type 2>&1 |tee ${TOPDIR}/LOGDIR/build_$(date +"%Y_%m_%d_%H_%M_%S").log; then
           return 1
       fi
    else
       if ! RECOMPILE_KERNEL=${RECOMPILE_KERNEL:-1} ./kernel_platform/build/android/prepare_vendor.sh $variants_platform $variants_type 2>&1 |tee ${TOPDIR}/LOGDIR/build_$(date +"%Y_%m_%d_%H_%M_%S").log; then
           return 1
       fi
    fi
    TOPDIR="${TOPDIR}" STOCK_VENDOR_BOOT="${STOCK_VENDOR_BOOT:-${TOPDIR}/vendor_boot.img}" \
        "${TOPDIR}/kernel_platform/oplus/build/repack_stock_flatten_vendor_boot.sh" \
        "${variants_platform}" "${variants_type}" || return 1
    TOPDIR="${TOPDIR}" STOCK_MODULE_ZIP="${STOCK_MODULE_ZIP:-${TOPDIR}/../dlkm/module.zip}" \
        "${TOPDIR}/kernel_platform/oplus/build/repack_stock_flatten_dlkm.sh" \
        "${variants_platform}" "${variants_type}" || return 1
    build_end_time
}

build_kernel_cmd
