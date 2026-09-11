#!/bin/bash

# Rebuild the final DLKM images after prepare_vendor.sh has copied all Oplus
# and techpack modules. The stock module archive is used as the authoritative
# partition manifest; a source-built module always wins over the stock binary.

set -euo pipefail

TOPDIR=${TOPDIR:?TOPDIR must point at the Android kernel workspace}
PLATFORM=${1:?missing platform name}
VARIANT=${2:?missing build variant}
STOCK_MODULE_ZIP="${TOPDIR}/modules.zip"

IMAGE_DIST="${TOPDIR}/kernel_platform/out/msm-kernel-${PLATFORM}-${VARIANT}/dist"
ANDROID_DIST="${TOPDIR}/out/dist"
DEVICE_KERNEL_OUT="${TOPDIR}/device/qcom/${PLATFORM}-kernel"
COMMON_MODULES_STAGING="${TOPDIR}/kernel_platform/bazel-bin/common/kernel_aarch64_modules_install/staging"
TOOLS_BIN="${TOPDIR}/kernel_platform/prebuilts/kernel-build-tools/linux_musl-x86/bin"
BUILD_IMAGE="${TOOLS_BIN}/build_image"
AVBTOOL="${TOOLS_BIN}/avbtool"
LLVM_STRIP=${LLVM_STRIP:-$(command -v llvm-strip || true)}

if [[ ! -f "${STOCK_MODULE_ZIP}" ]]; then
    echo "ERROR: stock module archive not found: ${STOCK_MODULE_ZIP}" >&2
    echo "Set STOCK_MODULE_ZIP=/absolute/path/to/modules.zip" >&2
    exit 1
fi

for required in "${BUILD_IMAGE}" "${AVBTOOL}"; do
    if [[ ! -x "${required}" ]]; then
        echo "ERROR: required image tool not found: ${required}" >&2
        exit 1
    fi
done
if [[ ! -x "${LLVM_STRIP}" ]]; then
    echo "ERROR: llvm-strip is required to package source-built modules" >&2
    exit 1
fi

work_dir=$(mktemp -d "${TMPDIR:-/tmp}/canoe-dlkm.XXXXXX")
trap 'rm -rf -- "${work_dir}"' EXIT
unzip -qq -o "${STOCK_MODULE_ZIP}" -d "${work_dir}/stock"

stock_system="${work_dir}/stock/system_dlkm/lib/modules"
stock_vendor="${work_dir}/stock/vendor_dlkm/lib/modules"
for required in "${stock_system}/modules.load" "${stock_vendor}/modules.load"; do
    if [[ ! -f "${required}" ]]; then
        echo "ERROR: invalid stock module archive; missing ${required}" >&2
        exit 1
    fi
done

mkdir -p "${work_dir}/selected/system" "${work_dir}/selected/vendor"
: > "${work_dir}/source-system.list"
: > "${work_dir}/source-vendor.list"
: > "${work_dir}/fallback-stock.list"

find_source_module() {
    local partition=$1
    local module_name=$2
    local candidate=""

    if [[ "${partition}" == "vendor" && -d "${DEVICE_KERNEL_OUT}/vendor_dlkm" ]]; then
        candidate=$(find -L "${DEVICE_KERNEL_OUT}/vendor_dlkm" -type f -name "${module_name}" -print -quit)
    fi
    if [[ -z "${candidate}" && -d "${COMMON_MODULES_STAGING}" ]]; then
        candidate=$(find -L "${COMMON_MODULES_STAGING}" -type f -name "${module_name}" -print -quit)
    fi
    if [[ -z "${candidate}" ]]; then
        candidate=$(find -L "${IMAGE_DIST}" -maxdepth 1 -type f -name "${module_name}" -print -quit)
    fi
    printf '%s' "${candidate}"
}

select_partition_modules() {
    local partition=$1
    local stock_dir=$2
    local selected_dir="${work_dir}/selected/${partition}"
    local stock_module module_name source_module

    while IFS= read -r stock_module; do
        module_name=$(basename "${stock_module}")
        source_module=$(find_source_module "${partition}" "${module_name}")
        if [[ -n "${source_module}" ]]; then
            install -m 0644 "${source_module}" "${selected_dir}/${module_name}"
            # External-module staging contains full DWARF objects (over 2 GiB
            # for canoe). Android DLKM packaging uses INSTALL_MOD_STRIP=1;
            # mirror that here while keeping stock fallback binaries intact.
            "${LLVM_STRIP}" --strip-debug "${selected_dir}/${module_name}"
            printf '%s\n' "${module_name}" >> "${work_dir}/source-${partition}.list"
        else
            install -m 0644 "${stock_module}" "${selected_dir}/${module_name}"
            printf '%s/%s\n' "${partition}" "${module_name}" >> "${work_dir}/fallback-stock.list"
        fi
    done < <(find "${stock_dir}" -maxdepth 1 -type f -name '*.ko' | sort)
}

select_partition_modules system "${stock_system}"
select_partition_modules vendor "${stock_vendor}"

system_count=$(find "${work_dir}/selected/system" -type f -name '*.ko' | wc -l)
vendor_count=$(find "${work_dir}/selected/vendor" -type f -name '*.ko' | wc -l)
stock_system_count=$(find "${stock_system}" -maxdepth 1 -type f -name '*.ko' | wc -l)
stock_vendor_count=$(find "${stock_vendor}" -maxdepth 1 -type f -name '*.ko' | wc -l)
if [[ "${system_count}" -ne "${stock_system_count}" || "${vendor_count}" -ne "${stock_vendor_count}" ]]; then
    echo "ERROR: selected module counts do not match stock: system=${system_count}/${stock_system_count} vendor=${vendor_count}/${stock_vendor_count}" >&2
    exit 1
fi

source_probe=$(find "${work_dir}/selected/system" -type f -name '*.ko' -print -quit)
kernel_release=$(modinfo -F vermagic "${source_probe}" | awk '{print $1}')
if [[ -z "${kernel_release}" ]]; then
    echo "ERROR: cannot determine kernel release from built modules" >&2
    exit 1
fi

prepare_depmod_tree() {
    local partition=$1
    local stage="${work_dir}/depmod-${partition}"
    local release_dir="${stage}/lib/modules/${kernel_release}"
    local module

    mkdir -p "${release_dir}"
    : > "${release_dir}/modules.order"
    : > "${release_dir}/modules.builtin"
    : > "${release_dir}/modules.builtin.modinfo"
    if [[ "${partition}" == "system" ]]; then
        cp "${work_dir}/selected/system/"*.ko "${release_dir}/"
    else
        cp "${work_dir}/selected/vendor/"*.ko "${release_dir}/"
        while IFS= read -r module; do
            module=$(basename "${module}")
            if [[ ! -e "${release_dir}/${module}" ]]; then
                cp "${work_dir}/selected/system/${module}" "${release_dir}/${module}"
            fi
        done < "${stock_system}/modules.load"
    fi
    "${TOOLS_BIN}/depmod" -b "${stage}" "${kernel_release}"
}

prepare_depmod_tree system
prepare_depmod_tree vendor

flatten_dep_file() {
    local partition=$1
    local input=$2
    local output=$3
    local prefix="/${partition}/lib/modules"
    local module

    sed -E "s#(^|[ :])([^ /:]+\\.ko)#\\1${prefix}/\\2#g" "${input}" > "${output}"
    if [[ "${partition}" == "vendor" ]]; then
        while IFS= read -r module; do
            module=$(basename "${module}")
            if [[ ! -e "${work_dir}/selected/vendor/${module}" ]]; then
                sed -i "s#/${partition}/lib/modules/${module}#/system/lib/modules/${module}#g" "${output}"
            fi
        done < "${stock_system}/modules.load"
    fi
}

make_flat_root() {
    local partition=$1
    local stock_root="${work_dir}/stock/${partition}_dlkm"
    local flat_root="${work_dir}/flat-${partition}"
    local depmod_root="${work_dir}/depmod-${partition}/lib/modules/${kernel_release}"

    mkdir -p "${flat_root}/lib/modules"
    cp "${work_dir}/selected/${partition}/"*.ko "${flat_root}/lib/modules/"
    flatten_dep_file "${partition}" "${depmod_root}/modules.dep" "${flat_root}/lib/modules/modules.dep"
    cp "${depmod_root}/modules.alias" "${flat_root}/lib/modules/modules.alias"
    cp "${stock_root}/lib/modules/modules.load" "${flat_root}/lib/modules/modules.load"

    for metadata in modules.blocklist modules.softdep system_dlkm.modules.blocklist; do
        if [[ -f "${stock_root}/lib/modules/${metadata}" ]]; then
            cp "${stock_root}/lib/modules/${metadata}" "${flat_root}/lib/modules/${metadata}"
        fi
    done
    if [[ -d "${stock_root}/etc" ]]; then
        cp -a "${stock_root}/etc" "${flat_root}/"
    fi
}

make_flat_root system
make_flat_root vendor

build_ext4_dlkm() {
    local partition=$1
    local flat_root="${work_dir}/flat-${partition}"
    local props="${work_dir}/${partition}.props"
    local contexts="${work_dir}/${partition}.file_contexts"
    local output="${work_dir}/${partition}_dlkm.img"

    if [[ "${partition}" == "system" ]]; then
        printf '/system_dlkm(/.*)? u:object_r:system_dlkm_file:s0\n' > "${contexts}"
    else
        printf '/vendor_dlkm(/.*)? u:object_r:vendor_file:s0\n' > "${contexts}"
        printf '/vendor_dlkm/lib/modules(/.*)? u:object_r:vendor_kernel_modules:s0\n' >> "${contexts}"
    fi
    {
        printf 'fs_type=ext4\n'
        printf 'use_dynamic_partition_size=true\n'
        printf 'ext_mkuserimg=mkuserimg_mke2fs\n'
        printf 'ext4_share_dup_blocks=true\n'
        printf 'extfs_rsv_pct=0\n'
        printf 'journal_size=0\n'
        printf 'mount_point=%s_dlkm\n' "${partition}"
        printf 'selinux_fc=%s\n' "${contexts}"
    } > "${props}"

    PATH="${TOOLS_BIN}:${PATH}" "${BUILD_IMAGE}" "${flat_root}" "${props}" "${output}" /dev/null
    PATH="${TOOLS_BIN}:${PATH}" "${AVBTOOL}" add_hashtree_footer \
        --partition_name "${partition}_dlkm" \
        --hash_algorithm sha256 \
        --image "${output}"

    install -m 0644 "${output}" "${IMAGE_DIST}/${partition}_dlkm.img"
    if [[ "${partition}" == "system" ]]; then
        install -m 0644 "${output}" "${IMAGE_DIST}/system_dlkm.ext4.img"
    fi
    if [[ -d "${ANDROID_DIST}" ]]; then
        install -m 0644 "${output}" "${ANDROID_DIST}/${partition}_dlkm.img"
        if [[ "${partition}" == "system" ]]; then
            install -m 0644 "${output}" "${ANDROID_DIST}/system_dlkm.ext4.img"
        fi
    fi
}

build_ext4_dlkm system
build_ext4_dlkm vendor

{
    printf 'kernel_release=%s\n' "${kernel_release}"
    printf 'system_modules=%s (stock=%s)\n' "${system_count}" "${stock_system_count}"
    printf 'vendor_modules=%s (stock=%s)\n' "${vendor_count}" "${stock_vendor_count}"
    printf 'system_source_modules=%s\n' "$(wc -l < "${work_dir}/source-system.list")"
    printf 'vendor_source_modules=%s\n' "$(wc -l < "${work_dir}/source-vendor.list")"
    printf 'stock_fallback_modules=%s\n' "$(wc -l < "${work_dir}/fallback-stock.list")"
    sed 's/^/stock_fallback=/' "${work_dir}/fallback-stock.list"
} > "${IMAGE_DIST}/dlkm_module_report.txt"
if [[ -d "${ANDROID_DIST}" ]]; then
    install -m 0644 "${IMAGE_DIST}/dlkm_module_report.txt" "${ANDROID_DIST}/dlkm_module_report.txt"
fi

echo "DLKM images rebuilt as flattened ext4: system=${system_count}, vendor=${vendor_count}"
