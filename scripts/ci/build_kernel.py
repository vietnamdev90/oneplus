#!/usr/bin/env python3
"""Build canoe kernel/dist targets, or warm and verify their Bazel download cache.

Uses the query and dist layout from soc-repo/build_with_bazel.py. Calls the
repository's Kleaf launcher directly so CI can apply the same cache flags to
query, analysis, build and run, without entering the stock-image repack scripts.
"""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys


def run(args, cwd, env, capture=False):
    print("+ " + " ".join(str(a) for a in args), flush=True)
    return subprocess.run([str(a) for a in args], cwd=cwd, env=env, check=True,
                          text=True, stdout=subprocess.PIPE if capture else None)


def require(path, executable=False):
    if not path.is_file() or (executable and not os.access(path, os.X_OK)):
        raise ValueError(f"Missing {'executable' if executable else 'file'}: {path}")


def select_targets(output, variant):
    prefix = f"//soc-repo:canoe_{variant}"
    labels = set(output.splitlines())
    selected = [label for label in labels if label.startswith(prefix + "_")
                and label.endswith("_dist") and not label.endswith("_abl_dist")]
    if prefix + "_dist" not in selected:
        raise ValueError(f"Bazel did not return {prefix}_dist")
    if any(not re.fullmatch(r"//soc-repo:[A-Za-z0-9_.-]+", s) for s in selected):
        raise ValueError("Unexpected Bazel target label")
    return sorted(selected, key=lambda label: (len(label), label))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--variant", choices=["perf", "consolidate"], default="perf")
    parser.add_argument("--jobs", type=int, default=min(os.cpu_count() or 2, 4))
    parser.add_argument("--warm", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.jobs <= 64:
        raise ValueError("jobs must be between 1 and 64")
    root = Path(args.root).resolve()
    if re.search(r"\s|%", str(root)):
        raise ValueError("Use a checkout path without whitespace or '%' for Bazel")
    kp = root / "kernel_platform"
    cache = root / ".ci-cache/bazel-repository"
    cache.mkdir(parents=True, exist_ok=True)
    (root / "logs").mkdir(exist_ok=True)

    for relative in ["common/Makefile", "common/build.config.constants", "soc-repo/BUILD.bazel",
                     "MODULE.bazel", "oplus/bazel/oplus_modules_variant.sh"]:
        require(kp / relative)
    constants = dict(re.findall(r"^(CLANG_VERSION|RUSTC_VERSION)=(\S+)",
                               (kp / "common/build.config.constants").read_text(), re.M))
    clang = kp / f"prebuilts/clang/host/linux-x86/clang-{constants['CLANG_VERSION']}/bin/clang"
    rust = kp / f"prebuilts/rust/linux-x86/{constants['RUSTC_VERSION']}/bin/rustc"
    bazel = kp / "tools/bazel"
    for path in [clang, rust, bazel,
                 kp / "prebuilts/kernel-build-tools/bazel/linux-x86_64/bazel",
                 kp / "prebuilts/build-tools/linux_musl-x86/bin/py3-cmd"]:
        require(path, executable=True)
    for path in ["kernel_platform/common", "kernel_platform/soc-repo", "vendor/oplus"]:
        if not (root / path).is_dir():
            raise ValueError(f"Incomplete kernel source checkout: {path}")

    source_sha = run(["git", "rev-parse", "HEAD"], root, os.environ, True).stdout.strip()
    version_fields = dict(re.findall(r"^(VERSION|PATCHLEVEL|SUBLEVEL)\s*=\s*(\d+)",
                                    (kp / "common/Makefile").read_text(), re.M))
    kernel_version = ".".join(version_fields[k] for k in ["VERSION", "PATCHLEVEL", "SUBLEVEL"])
    env = os.environ.copy()
    env.update({"TOPDIR": str(root), "ANDROID_BUILD_TOP": str(root),
                "CHIPSET_COMPANY": "QCOM", "OPLUS_VND_BUILD_PLATFORM": "SM8850",
                "TARGET_BOARD_PLATFORM": "canoe",
                "ANDROID_PRODUCT_OUT": str(root / "out/target/product/canoe"),
                "OPLUS_USE_JFROG_CACHE": "false", "OPLUS_USE_BUILDBUDDY_REMOTE_BUILD": "false"})
    # Bazel's mirrored executable supplies its own JVM. The OEM setup script's
    # top-level prebuilts/jdk/jdk21 path is absent from this standalone repository.
    env.pop("JAVA_HOME", None)
    env.pop("LTO", None)
    Path(env["ANDROID_PRODUCT_OUT"]).mkdir(parents=True, exist_ok=True)
    run([clang, "--version"], root, env)
    run([rust, "--version"], root, env)

    # The repository tracks workstation-specific generated Bazel symlinks.
    for name in ["bazel-bin", "bazel-out", "bazel-testlogs", "bazel-kernel_platform"]:
        link = kp / name
        if link.is_symlink():
            link.unlink()
    rc = kp / "user.bazelrc"
    previous_rc = rc.read_bytes() if rc.exists() else None
    if rc.is_symlink():
        raise ValueError("Refusing to overwrite a symlinked user.bazelrc")
    flags = ["startup --host_jvm_args=-Xmx3g",
             f"common --repository_cache={cache}",
             "common --noenable_workspace",
             "build --incompatible_sandbox_hermetic_tmp=false",
             "build --//soc-repo:skip_abl=true",
             f"build --jobs={args.jobs}",
             "build --local_ram_resources=8192"]
    if not args.warm:
        flags.append("common --repository_disable_download")
    rc.write_bytes((previous_rc or b"") + b"\n" + ("\n".join(flags) + "\n").encode())
    variants = ["perf", "consolidate"] if args.warm else [args.variant]
    command = [bazel, f"--output_user_root={root / '.ci-cache/bazel-user'}"]
    try:
        for variant in variants:
            # Match prepare_vendor.sh's generated Oplus configuration before query.
            features = " ".join(f"{k}={v}" for k, v in sorted(env.items())
                                if k.startswith("OPLUS_FEATURE_BSP_"))
            run(["bash", kp / "oplus/bazel/oplus_modules_variant.sh", "canoe", variant, features], root, env)
            query = (f'filter("canoe_{variant}.*_dist$", '
                     'attr(generator_function, define_canoe, soc-repo/...))')
            output = run(command + ["query", "--noshow_progress", query], kp, env, True).stdout
            labels = select_targets(output, variant)
            metadata = {"source_sha": source_sha, "kernel_version": kernel_version,
                        "variant": variant, "toolchain": constants, "targets": labels,
                        "dependency_analysis_only": args.warm}
            (root / "logs" / f"build-{variant}.json").write_text(json.dumps(metadata, indent=2) + "\n")
            if args.warm:
                # Resolve all rule/toolchain dependencies for BOTH variants, then
                # repeat with downloads prohibited to prove the cache is sufficient
                # for analysis. This does not claim a successful kernel compilation.
                run(command + ["build", "--nobuild", *labels], kp, env)
                run(command + ["shutdown"], kp, env)
                proof = root / f".ci-cache/bazel-proof-{variant}"
                if proof.exists():
                    raise ValueError(f"Cache verification needs a fresh Bazel output directory: {proof}")
                proof_command = [bazel, f"--output_user_root={proof}"]
                run(proof_command + ["build", "--nobuild", "--repository_disable_download", *labels], kp, env)
                run(proof_command + ["shutdown"], kp, env)
            else:
                run(command + ["build", *labels], kp, env)
                out = kp / f"out/msm-kernel-canoe-{variant}"
                for label in labels:
                    target_args = []
                    if "_all_oplus_ddk_modules_dist" not in label:
                        subdir = "host" if re.search(r"_(dtc|host)_dist$", label) else "dist"
                        target_args = ["--", "--dist_dir", out / subdir]
                    run(command + ["run", label, *target_args], kp, env)
                require(out / "dist/Image")
                print(f"Kernel {kernel_version}: {out / 'dist/Image'}", flush=True)
        if args.warm:
            (root / ".ci-cache/bazel-warmed.json").write_text(json.dumps(
                {"source_sha": source_sha, "variants": variants}) + "\n")
    finally:
        if previous_rc is None:
            rc.unlink(missing_ok=True)
        else:
            rc.write_bytes(previous_rc)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
