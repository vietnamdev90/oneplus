load("//build/bazel_common_rules/dist:dist.bzl", "copy_to_dist_dir")


def define_techpack_modules(target, msm_target, variant):
    """Build Qualcomm out-of-tree modules shipped by the canoe stock kernel.

    The public source drop does not contain device/qcom/canoe/techpack_modules.bzl,
    so prepare_vendor.sh falls back to this file.  Keeping this function empty
    silently omits all Qualcomm techpack modules from the normal kernel dist.
    """
    if msm_target != "canoe":
        return

    techpack_targets = [
        # Audio
        "//vendor/qcom/opensource/audio-kernel:{}_modules".format(target),

        # Bluetooth
        "//vendor/qcom/opensource/bt-kernel:{}_bt_fm_swr".format(target),
        "//vendor/qcom/opensource/bt-kernel:{}_btfm_slim_codec".format(target),
        "//vendor/qcom/opensource/bt-kernel:{}_btfmcodec".format(target),
        "//vendor/qcom/opensource/bt-kernel:{}_btpower".format(target),
        "//vendor/qcom/opensource/bt-kernel:{}_radio-i2c-rtc6226-qca".format(target),

        # Camera and shared-memory mailbox
        "//vendor/qcom/opensource/camera-kernel:{}_camera".format(target),
        "//vendor/qcom/opensource/data-kernel/drivers/smem-mailbox:{}_smem_mailbox".format(target),

        # IPA and rmnet data path
        "//vendor/qcom/opensource/dataipa:{}_gsim".format(target),
        "//vendor/qcom/opensource/dataipa:{}_ipam".format(target),
        "//vendor/qcom/opensource/dataipa:{}_ipanetm".format(target),
        "//vendor/qcom/opensource/datarmnet:{}_rmnet_core".format(target),
        "//vendor/qcom/opensource/datarmnet:{}_rmnet_ctl".format(target),
        "//vendor/qcom/opensource/datarmnet-ext/aps:{}_aps".format(target),
        "//vendor/qcom/opensource/datarmnet-ext/mem:{}_rmnet_mem".format(target),
        "//vendor/qcom/opensource/datarmnet-ext/offload:{}_offload".format(target),
        "//vendor/qcom/opensource/datarmnet-ext/perf:{}_perf".format(target),
        "//vendor/qcom/opensource/datarmnet-ext/perf_tether:{}_perf_tether".format(target),
        "//vendor/qcom/opensource/datarmnet-ext/sch:{}_sch".format(target),
        "//vendor/qcom/opensource/datarmnet-ext/shs:{}_shs".format(target),
        "//vendor/qcom/opensource/datarmnet-ext/wlan:{}_wlan".format(target),

        # Display, DSP, EVA, GPU, fences and video
        "//vendor/qcom/opensource/display-drivers:{}_msm_drm".format(target),
        "//vendor/qcom/opensource/dsp-kernel:{}_frpc-adsprpc".format(target),
        "//vendor/qcom/opensource/eva-kernel:{}_eva_modules".format(target),
        "//vendor/qcom/opensource/graphics-kernel:{}_msm_kgsl".format(target),
        "//vendor/qcom/opensource/mm-drivers/hfi_core:{}_msm_hfi_core".format(target),
        "//vendor/qcom/opensource/mm-drivers/hw_fence:{}_msm_hw_fence".format(target),
        "//vendor/qcom/opensource/mm-drivers/msm_ext_display:{}_msm_ext_display".format(target),
        "//vendor/qcom/opensource/mm-drivers/sync_fence:{}_sync_fence".format(target),
        "//vendor/qcom/opensource/mmrm-driver:{}_mmrm_driver".format(target),
        "//vendor/qcom/opensource/synx-kernel:{}_modules".format(target),
        "//vendor/qcom/opensource/video-driver:{}_msm_video".format(target),

        # Secure and SPU modules present in the stock vendor_dlkm image
        "//vendor/qcom/opensource/securemsm-kernel:{}_hdcp_qseecom_dlkm".format(target),
        "//vendor/qcom/opensource/securemsm-kernel:{}_qce50_dlkm".format(target),
        "//vendor/qcom/opensource/securemsm-kernel:{}_qcedev-mod_dlkm".format(target),
        "//vendor/qcom/opensource/securemsm-kernel:{}_qcrypto-msm_dlkm".format(target),
        "//vendor/qcom/opensource/securemsm-kernel:{}_qrng_dlkm".format(target),
        "//vendor/qcom/opensource/securemsm-kernel:{}_smcinvoke_dlkm".format(target),
        "//vendor/qcom/opensource/securemsm-kernel:{}_smmu_proxy_dlkm".format(target),
        "//vendor/qcom/opensource/securemsm-kernel:{}_tmecom-intf_dlkm".format(target),
        "//vendor/qcom/opensource/securemsm-kernel:{}_tz_log_dlkm".format(target),
        "//vendor/qcom/opensource/spu-kernel:{}_spcom".format(target),
        "//vendor/qcom/opensource/spu-kernel:{}_spss_utils".format(target),

        # NFC and secure-element drivers
        "//vendor/nxp/opensource/driver:{}_nxp-nci".format(target),
        "//vendor/st/opensource/driver:{}_stm_nfc_i2c".format(target),
        "//vendor/st/opensource/eSE-driver:{}_stm_st54se_gpio".format(target),

        # WLAN platform and the three stock CLD chip variants
        "//vendor/qcom/opensource/wlan/platform:{}_cnss2".format(target),
        "//vendor/qcom/opensource/wlan/platform:{}_cnss_nl".format(target),
        "//vendor/qcom/opensource/wlan/platform:{}_cnss_plat_ipc_qmi_svc".format(target),
        "//vendor/qcom/opensource/wlan/platform:{}_cnss_prealloc".format(target),
        "//vendor/qcom/opensource/wlan/platform:{}_cnss_utils".format(target),
        "//vendor/qcom/opensource/wlan/platform:{}_icnss2".format(target),
        "//vendor/qcom/opensource/wlan/platform:{}_wlan_firmware_service".format(target),
        "//vendor/qcom/opensource/wlan/qcacld-3.0:{}_qca_cld_kiwi-v2".format(target),
        "//vendor/qcom/opensource/wlan/qcacld-3.0:{}_qca_cld_peach-v2".format(target),
        "//vendor/qcom/opensource/wlan/qcacld-3.0:{}_qca_cld_wcn7750".format(target),
    ]

    copy_to_dist_dir(
        name = "{}_techpack_modules_dist".format(target),
        data = techpack_targets,
        dist_dir = "out/msm-kernel-{}-{}/dist".format(msm_target, variant),
        flat = True,
        wipe_dist_dir = False,
        allow_duplicate_filenames = False,
        mode_overrides = {"**/*": "644"},
        log = "info",
    )
