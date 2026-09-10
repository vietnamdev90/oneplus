#!/bin/bash

# Repack vendor_boot with the stock module manifest. Source-built modules are
# preferred; a stock binary is used only when this tree cannot build that name.

set -euo pipefail

TOPDIR=${TOPDIR:?TOPDIR must point at the Android kernel workspace}
PLATFORM=${1:?missing platform name}
VARIANT=${2:?missing build variant}
STOCK_VENDOR_BOOT=${STOCK_VENDOR_BOOT:-${TOPDIR}/vendor_boot.img}

IMAGE_DIST="${TOPDIR}/kernel_platform/out/msm-kernel-${PLATFORM}-${VARIANT}/dist"
ANDROID_DIST="${TOPDIR}/out/dist"
DEVICE_KERNEL_OUT="${TOPDIR}/device/qcom/${PLATFORM}-kernel"
COMMON_MODULES_STAGING="${TOPDIR}/kernel_platform/bazel-bin/common/kernel_aarch64_modules_install/staging"
TOOLS_BIN="${TOPDIR}/kernel_platform/prebuilts/kernel-build-tools/linux_musl-x86/bin"
UNPACK_TOOL="${TOPDIR}/kernel_platform/tools/mkbootimg/unpack_bootimg.py"
MKBOOTIMG_TOOL="${TOPDIR}/kernel_platform/tools/mkbootimg/mkbootimg.py"
CURRENT_VENDOR_BOOT="${IMAGE_DIST}/vendor_boot.img"
MERGED_DTB="${DEVICE_KERNEL_OUT}/dtbs/dtb.img"
LLVM_STRIP=${LLVM_STRIP:-$(command -v llvm-strip || true)}
PARTITION_SIZE=$((0x6000000))

for required in \
    "${STOCK_VENDOR_BOOT}" "${CURRENT_VENDOR_BOOT}" "${MERGED_DTB}" \
    "${UNPACK_TOOL}" "${MKBOOTIMG_TOOL}" "${TOOLS_BIN}/avbtool" \
    "${TOOLS_BIN}/lz4" "${TOOLS_BIN}/mkbootfs" "${TOOLS_BIN}/depmod" \
    "${LLVM_STRIP}"; do
    if [[ ! -e "${required}" ]]; then
        echo "ERROR: cannot repack vendor_boot; missing ${required}" >&2
        exit 1
    fi
done

work_dir=$(mktemp -d "${TMPDIR:-/tmp}/${PLATFORM}-vendor-boot.XXXXXX")
trap 'rm -rf -- "${work_dir}"' EXIT
mkdir -p "${work_dir}/stock-unpacked" "${work_dir}/ramdisk"

boot_args=$(python3 "${UNPACK_TOOL}" \
    --boot_img "${STOCK_VENDOR_BOOT}" \
    --out "${work_dir}/stock-unpacked" \
    --format=mkbootimg)

"${TOOLS_BIN}/lz4" -dc "${work_dir}/stock-unpacked/vendor_ramdisk00" | \
    (cd "${work_dir}/ramdisk" && cpio -idm --no-absolute-filenames >/dev/null 2>&1)

stock_modules="${work_dir}/ramdisk/lib/modules"
flat_modules="${work_dir}/selected-modules"
mkdir -p "${flat_modules}"
: > "${work_dir}/source.list"
: > "${work_dir}/fallback.list"

if [[ ! -f "${stock_modules}/modules.load" ]]; then
    echo "ERROR: stock vendor_boot does not contain /lib/modules/modules.load" >&2
    exit 1
fi
if [[ ! -f "${work_dir}/ramdisk/first_stage_ramdisk/fstab.qcom" ]]; then
    echo "ERROR: stock vendor_boot does not contain first_stage_ramdisk/fstab.qcom" >&2
    exit 1
fi

find_source_module() {
    local module_name=$1
    local candidate=""

    if [[ -d "${DEVICE_KERNEL_OUT}/vendor_dlkm" ]]; then
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

while IFS= read -r stock_module; do
    module_name=$(basename "${stock_module}")
    source_module=$(find_source_module "${module_name}")
    if [[ -n "${source_module}" ]]; then
        install -m 0644 "${source_module}" "${flat_modules}/${module_name}"
        "${LLVM_STRIP}" --strip-debug "${flat_modules}/${module_name}"
        printf '%s\n' "${module_name}" >> "${work_dir}/source.list"
    else
        install -m 0644 "${stock_module}" "${flat_modules}/${module_name}"
        printf '%s\n' "${module_name}" >> "${work_dir}/fallback.list"
    fi
done < <(find "${stock_modules}" -maxdepth 1 -type f -name '*.ko' | sort)

stock_count=$(find "${stock_modules}" -maxdepth 1 -type f -name '*.ko' | wc -l)
selected_count=$(find "${flat_modules}" -maxdepth 1 -type f -name '*.ko' | wc -l)
if [[ "${selected_count}" -ne "${stock_count}" ]]; then
    echo "ERROR: vendor_boot module count mismatch: ${selected_count}/${stock_count}" >&2
    exit 1
fi

source_probe=$(find "${flat_modules}" -maxdepth 1 -type f -name '*.ko' -print -quit)
kernel_release=$(modinfo -F vermagic "${source_probe}" | awk '{print $1}')
if [[ -z "${kernel_release}" ]]; then
    echo "ERROR: cannot determine release of selected vendor_boot modules" >&2
    exit 1
fi
while IFS= read -r module; do
    module_release=$(modinfo -F vermagic "${module}" | awk '{print $1}')
    if [[ "${module_release}" != "${kernel_release}" ]]; then
        echo "ERROR: vermagic mismatch in $(basename "${module}"): ${module_release} != ${kernel_release}" >&2
        exit 1
    fi
done < <(find "${flat_modules}" -maxdepth 1 -type f -name '*.ko' | sort)

depmod_release="${work_dir}/depmod/lib/modules/${kernel_release}"
mkdir -p "${depmod_release}"
cp "${flat_modules}/"*.ko "${depmod_release}/"
: > "${depmod_release}/modules.order"
: > "${depmod_release}/modules.builtin"
: > "${depmod_release}/modules.builtin.modinfo"
"${TOOLS_BIN}/depmod" -b "${work_dir}/depmod" "${kernel_release}"
sed -E 's#(^|[ :])([^ /:]+\.ko)#\1/lib/modules/\2#g' \
    "${depmod_release}/modules.dep" > "${flat_modules}/modules.dep"
cp "${depmod_release}/modules.alias" "${flat_modules}/modules.alias"

for metadata in modules.blocklist modules.load modules.load.recovery modules.options modules.softdep; do
    if [[ -f "${stock_modules}/${metadata}" ]]; then
        cp "${stock_modules}/${metadata}" "${flat_modules}/${metadata}"
    fi
done

rm -rf -- "${work_dir}/ramdisk/lib/modules"
mkdir -p "${work_dir}/ramdisk/lib"
mv "${flat_modules}" "${work_dir}/ramdisk/lib/modules"
"${TOOLS_BIN}/mkbootfs" "${work_dir}/ramdisk" | \
    "${TOOLS_BIN}/lz4" -c -l -12 --favor-decSpeed > \
    "${work_dir}/stock-unpacked/vendor_ramdisk00"
cp "${MERGED_DTB}" "${work_dir}/stock-unpacked/dtb"

bash -c "python3 '${MKBOOTIMG_TOOL}' ${boot_args} --vendor_boot '${work_dir}/vendor_boot.img'"
"${TOOLS_BIN}/avbtool" add_hash_footer \
    --image "${work_dir}/vendor_boot.img" \
    --algorithm NONE \
    --partition_size "${PARTITION_SIZE}" \
    --partition_name vendor_boot

install -m 0644 "${work_dir}/vendor_boot.img" "${CURRENT_VENDOR_BOOT}"
if [[ -d "${ANDROID_DIST}" ]]; then
    install -m 0644 "${CURRENT_VENDOR_BOOT}" "${ANDROID_DIST}/vendor_boot.img"
fi

{
    printf 'kernel_release=%s\n' "${kernel_release}"
    printf 'vendor_boot_modules=%s (stock=%s)\n' "${selected_count}" "${stock_count}"
    printf 'source_modules=%s\n' "$(wc -l < "${work_dir}/source.list")"
    printf 'stock_fallback_modules=%s\n' "$(wc -l < "${work_dir}/fallback.list")"
    sed 's/^/stock_fallback=/' "${work_dir}/fallback.list"
} > "${IMAGE_DIST}/vendor_boot_module_report.txt"
if [[ -d "${ANDROID_DIST}" ]]; then
    install -m 0644 "${IMAGE_DIST}/vendor_boot_module_report.txt" \
        "${ANDROID_DIST}/vendor_boot_module_report.txt"
fi

echo "vendor_boot rebuilt as flattened ramdisk: modules=${selected_count}/${stock_count}"
