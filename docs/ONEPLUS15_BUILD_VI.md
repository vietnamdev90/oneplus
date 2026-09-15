# Build OnePlus 15 bằng toolchain trong GitHub Releases

Áp dụng cho repo `vietnamdev90/oneplus`, dựa trên commit nguồn
`9f6fccdd3e2d7f4ad9f800bd268d2a2e6ddf2792` và file `oneplus_15.xml` đính kèm.
Tại commit đã kiểm tra: Android 16, kernel **6.12.23**, target **canoe**,
Clang **r536225**, Rust **1.82.0**. Script đọc phiên bản compiler từ
`kernel_platform/common/build.config.constants` khi chạy.

## Chạy trên GitHub Actions

1. Đưa toàn bộ các file trong bản thay đổi này vào repo, giữ nguyên đường dẫn.
   Cần cả hai YAML, hai script Python và `manifests/oneplus_15.xml`.
   Merge nhánh thay đổi vào `main` để các workflow thủ công xuất hiện trong Actions.
2. Vào **Actions → Mirror OnePlus 15 build tools → Run workflow**.
   Chờ job **Cache Bazel dependencies and publish ready index** thành công.
3. Vào **Actions → Build OnePlus 15 kernel from release tools → Run workflow**.
   Chọn `perf` hoặc `consolidate`; mặc định `perf`, runner `ubuntu-24.04`, `jobs=4`.
4. Tải artifact `oneplus15-kernel-...` ở cuối lần chạy. Log nằm trong artifact
   `oneplus15-build-logs-...`, kể cả khi build thất bại.

`cache_repository` để trống sẽ dùng Releases của chính repo đang chạy.
Có thể điền `vietnamdev90/oneplus` khi workflow nằm trong một fork công khai khác.
Token mặc định của workflow không có quyền đọc một repo riêng tư khác.

Nếu bật `publish_release`, job riêng sẽ xuất bản kết quả vào release
`kernel-oneplus15-<variant>-<run_id>-<attempt>`. Mặc định chỉ upload Actions artifact.
Job build chỉ có quyền đọc; job mirror/publish được cấp `contents: write`.

## Bộ công cụ được lưu

Manifest có **33 project**. Ba repo mã nguồn OnePlus lấy từ checkout hiện tại;
toàn bộ **30 project còn lại** được mirror, không lọc bằng một danh sách vài tên compiler.

| Nhóm | Nội dung |
| --- | --- |
| Compiler và runtime | Clang, Rust, clang-tools, GCC host/sysroot, NDK r26, JDK11 |
| Công cụ build | kernel-build-tools, build-tools, Bazel và Python đi kèm prebuilts |
| Bazel | bazel_common_rules, bazel_features, skylib, registry, platforms, rules_cc/license/pkg/python/shell, absl-py |
| Công cụ và thư viện phụ | qcom-dtc, lz4, pigz, toybox, zlib, zopfli, libufdt, mkbootimg |
| Project còn lại | trusty, asuite, tradefed |
| Dependency phát sinh | Bazel repository download cache sau khi phân tích cả perf và consolidate |

Mỗi project được fetch đúng SHA trong XML, tải nội dung Git LFS thật và submodule,
rồi đóng gói nội dung thư mục. Archive giữ file thực thi và symlink, không phụ thuộc
vào thư mục bọc khác nhau của GitHub/GitLab/Googlesource.

Các asset mới có tiền tố `v2-`; ID phụ thuộc URL, SHA và đường dẫn project.
`kernel/prebuilts/build-tools` và `kernel_platform/prebuilts/build-tools` có ID riêng.
Các asset cũ trong `toolchain-cache` được giữ nguyên. **Lần mirror v2 đầu tiên tải lại
từ upstream**, vì định dạng cũ chưa có danh sách mảnh và thông tin bố cục v2.
Các lần sau bỏ qua project đã có receipt và đầy đủ các mảnh hợp lệ.

Archive được chia thành mảnh tối đa **1500 MiB**. SHA-256 được lưu cho từng mảnh
và toàn bộ archive. Receipt của project được upload sau các mảnh. Index
`oneplus15-<manifest-hash>-v2.json` chỉ được xuất bản sau khi đủ 30 project và
phân tích dependency của cả hai biến thể thành công.

Index ghi rõ SHA manifest và SHA nguồn đã dùng để tạo cache Bazel. Khi manifest
thay đổi, workflow build yêu cầu index tương ứng; không tự lấy bộ compiler gần giống.
Nếu chỉ sửa mã C mà không đổi dependency, có thể dùng lại cache hiện có. Nếu đổi
rules, compiler hoặc dependency Bazel, chạy mirror lại trên nhánh mới trước khi build.

## Workflow build thực hiện gì?

- Checkout mã nguồn đang chọn, không clone lại ba repo OnePlusOSS để ghi đè các sửa đổi.
- Kiểm tra index, kích thước và checksum; ghép đúng thứ tự mọi mảnh rồi mới giải nén.
- Khôi phục prebuilts vào đúng `kernel_platform/prebuilts/...` và dựng lại các linkfile.
  Dependency ngoài prebuilts đã có trong checkout được giữ để bảo toàn các bản sửa.
  Report ghi từng project được khôi phục hoặc giữ từ source checkout.
- Sinh cấu hình Oplus bằng `oplus_modules_variant.sh`, query các target
  `canoe_<variant>..._dist`, build rồi xuất dist và modules Oplus.
- Dùng `kernel_platform/tools/bazel` của repo; compiler được chọn bởi cấu hình Kleaf.
  Bật `--repository_disable_download` để thiếu cache thì dừng, không âm thầm tải bù tool.
- Lưu source commit, phiên bản compiler, danh sách target, manifest và log cùng kết quả.

Luồng query/dist dựa trên `soc-repo/build_with_bazel.py`; script CI gọi trực tiếp
launcher Kleaf để áp dụng cache cho cả query, build và run. Target bootloader ABL
được loại khỏi build, đúng mặc định wrapper hiện có. Không sửa cấu hình kernel,
thay compiler, bỏ kiểm tra ABI hoặc chèn KernelSU/AnyKernel3.

Script `oplus_build_kernel.sh` ở commit đã kiểm tra còn gọi bước repack cần
`vendor_boot.img`, `modules.zip`, `system_dlkm.img`, `vendor_dlkm.img` của ROM gốc.
Workflow này dừng ở **kết quả biên dịch kernel/modules/dist**. Các ảnh do Kleaf sinh
chưa được repack theo ROM stock và chưa được kiểm tra boot trên điện thoại.

Một `linkfile` trong manifest trỏ đến `soc-repo/qcom_build_extensions`, nhưng thư mục
đó không có trong commit nguồn đã kiểm tra. Helper ghi rõ điều này và không tạo
thêm symlink hỏng; các quy tắc đang có trong repo vẫn được sử dụng.

## Giải nén artifact kernel

Sau khi giải nén ZIP artifact và chuyển vào thư mục có `SHA256SUMS`:

```bash
sha256sum -c SHA256SUMS
cat oneplus15-kernel.tar.gz.part-* > oneplus15-kernel.tar.gz
tar -xzf oneplus15-kernel.tar.gz
```

`Image`, `vmlinux`, cấu hình, modules và các đầu ra mà target tạo nằm dưới
`kernel_platform/out/msm-kernel-canoe-<variant>/`. Modules DDK Oplus, nếu được
xuất riêng, nằm dưới `device/qcom/canoe-kernel/oplus_ddk/` trong cùng archive.

## Dùng lại tools trên máy Linux

Cần Ubuntu 24.04 x86_64/Python 3.12+, `gh`, `git`, `pigz` và các gói host trong YAML.
Mirror đã thành công trên GitHub trước khi chạy các lệnh sau trong một checkout mới:

```bash
gh auth login
python3 scripts/ci/toolchain_cache.py restore \
  --repo vietnamdev90/oneplus --tag toolchain-cache \
  --manifest manifests/oneplus_15.xml
python3 scripts/ci/build_kernel.py --variant perf --jobs 4
```

Các lần build tiếp theo trong cùng checkout chỉ cần lệnh `build_kernel.py`.
Không chạy `restore` lên bộ prebuilts tự sửa hoặc trộn nhiều phiên bản;
helper chủ động từ chối ghi đè thư mục chưa có receipt của nó.

Đây là cache dependency để tái sử dụng; runner vẫn tải source, Actions và các gói
Ubuntu. Cờ chặn download của Bazel không phải cơ chế cô lập toàn bộ mạng của job.
Toolchain/binary trong manifest và archive dependency được lấy từ Releases.

## Kiểm tra và giới hạn

Đã kiểm tra parser YAML, cú pháp các khối shell, Python và các ca thử offline về
cache nhiều mảnh, mất mảnh, sai SHA, symlink, quyền thực thi và lựa chọn target.
Chưa chạy một lần mirror tải toàn bộ công cụ hoặc biên dịch kernel thật trong phiên này.
Job mirror có bước phân tích Bazel và kiểm tra lại từ thư mục output mới với download
bị chặn; bước đó không thay thế kiểm tra compile/boot thực tế.

Nếu upstream đã xóa hẳn commit/LFS object, mirror sẽ báo lỗi và không công bố index
hoàn chỉnh. Cần một mirror khác có đúng nội dung revision đó. Nếu máy build hết RAM,
đĩa hoặc vượt thời gian job, chọn runner Ubuntu 24.04 x86_64 lớn hơn qua input `runner`
và điều chỉnh `jobs`; không hạ compiler hoặc đổi cấu hình kernel để che lỗi tài nguyên.
Runner tự quản lý cần GitHub runner mới hỗ trợ `actions/checkout@v6`, `sudo` và apt.

Nguồn đối chiếu:

- https://github.com/vietnamdev90/oneplus/tree/9f6fccdd3e2d7f4ad9f800bd268d2a2e6ddf2792
- Hai file đính kèm: `mirror-toolchains.yml`, `oneplus_15.xml`.
- https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases
- https://bazel.build/versions/8.5.0/reference/command-line-reference
