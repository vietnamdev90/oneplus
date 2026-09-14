#!/usr/bin/env python3
"""Replace DLKM logical partitions in a raw Android super image."""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import struct
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


SECTOR_SIZE = 512
GEOMETRY_OFFSET = 4096
GEOMETRY_SIZE = 4096
GEOMETRY_MAGIC = 0x616C4467
HEADER_MAGIC = 0x414C5030
SPARSE_MAGIC = 0xED26FF3A
LINEAR_EXTENT = 0


class SuperImageError(RuntimeError):
    pass


@dataclass(frozen=True)
class Extent:
    sectors: int
    target_type: int
    target_data: int
    target_source: int

    @property
    def size(self) -> int:
        return self.sectors * SECTOR_SIZE


@dataclass(frozen=True)
class Partition:
    name: str
    extents: tuple[Extent, ...]

    @property
    def size(self) -> int:
        return sum(extent.size for extent in self.extents)


def checked_sha256(data: bytes, checksum_offset: int) -> bytes:
    mutable = bytearray(data)
    mutable[checksum_offset : checksum_offset + 32] = bytes(32)
    return hashlib.sha256(mutable).digest()


def read_metadata(super_image: Path) -> dict[str, Partition]:
    with super_image.open("rb") as image:
        magic_bytes = image.read(4)
        if len(magic_bytes) != 4:
            raise SuperImageError(f"super image is too small: {super_image}")
        if struct.unpack("<I", magic_bytes)[0] == SPARSE_MAGIC:
            raise SuperImageError(
                "stock super image is Android sparse; convert it with simg2img first"
            )

        image.seek(GEOMETRY_OFFSET)
        geometry = image.read(52)
        if len(geometry) != 52:
            raise SuperImageError("missing logical-partition geometry")
        magic, struct_size = struct.unpack_from("<II", geometry)
        if magic != GEOMETRY_MAGIC or struct_size < 52:
            raise SuperImageError("stock image does not contain valid liblp geometry")
        if checked_sha256(geometry[:struct_size], 8) != geometry[8:40]:
            raise SuperImageError("logical-partition geometry checksum mismatch")
        metadata_max_size, slot_count, logical_block_size = struct.unpack_from(
            "<III", geometry, 40
        )
        if logical_block_size % SECTOR_SIZE:
            raise SuperImageError(f"unsupported logical block size: {logical_block_size}")

        metadata_base = GEOMETRY_OFFSET + 2 * GEOMETRY_SIZE
        last_error = "no readable metadata slot"
        for slot in range(slot_count):
            metadata_offset = metadata_base + slot * metadata_max_size
            try:
                return read_metadata_slot(image, metadata_offset, metadata_max_size)
            except SuperImageError as error:
                last_error = str(error)
        raise SuperImageError(last_error)


def read_metadata_slot(image, offset: int, maximum_size: int) -> dict[str, Partition]:
    image.seek(offset)
    initial_header = image.read(128)
    if len(initial_header) != 128:
        raise SuperImageError("truncated metadata header")
    magic, major, _minor, header_size = struct.unpack_from("<IHHI", initial_header)
    if magic != HEADER_MAGIC or major != 10:
        raise SuperImageError("invalid logical-partition metadata header")
    if header_size < 128 or header_size > maximum_size:
        raise SuperImageError(f"invalid metadata header size: {header_size}")

    image.seek(offset)
    header = image.read(header_size)
    if checked_sha256(header, 12) != header[12:44]:
        raise SuperImageError("metadata header checksum mismatch")
    tables_size = struct.unpack_from("<I", header, 44)[0]
    if header_size + tables_size > maximum_size:
        raise SuperImageError("metadata tables exceed the configured maximum size")
    image.seek(offset + header_size)
    tables = image.read(tables_size)
    if len(tables) != tables_size or hashlib.sha256(tables).digest() != header[48:80]:
        raise SuperImageError("metadata tables checksum mismatch")

    descriptors = [struct.unpack_from("<III", header, 80 + i * 12) for i in range(4)]
    for table_offset, entry_count, entry_size in descriptors:
        if table_offset + entry_count * entry_size > len(tables):
            raise SuperImageError("metadata table descriptor is out of bounds")

    partition_desc, extent_desc, _group_desc, block_device_desc = descriptors
    if partition_desc[2] < 52 or extent_desc[2] < 24 or block_device_desc[2] < 64:
        raise SuperImageError("unsupported logical-partition metadata entry size")

    extents: list[Extent] = []
    extent_offset, extent_count, extent_size = extent_desc
    for index in range(extent_count):
        entry = extent_offset + index * extent_size
        extents.append(Extent(*struct.unpack_from("<QIQI", tables, entry)))

    block_device_count = block_device_desc[1]
    partitions: dict[str, Partition] = {}
    partition_offset, partition_count, partition_size = partition_desc
    for index in range(partition_count):
        entry = partition_offset + index * partition_size
        name = tables[entry : entry + 36].split(bytes(1), 1)[0].decode("ascii")
        first_extent, number_of_extents = struct.unpack_from("<II", tables, entry + 40)
        if first_extent + number_of_extents > len(extents):
            raise SuperImageError(f"partition {name} has invalid extent indexes")
        selected = tuple(extents[first_extent : first_extent + number_of_extents])
        for extent in selected:
            if extent.target_type != LINEAR_EXTENT:
                raise SuperImageError(
                    f"partition {name} uses unsupported extent type {extent.target_type}"
                )
            if extent.target_source >= block_device_count:
                raise SuperImageError(f"partition {name} refers to an invalid block device")
            if extent.target_source != 0:
                raise SuperImageError(
                    f"partition {name} uses block device {extent.target_source}; "
                    "multi-device super images are unsupported"
                )
        partitions[name] = Partition(name, selected)
    return partitions


def resolve_partition(
    partitions: dict[str, Partition], base_name: str, slot: str
) -> list[Partition]:
    if slot == "all":
        names = [base_name, f"{base_name}_a", f"{base_name}_b"]
    elif slot == "auto":
        names = [base_name, f"{base_name}_a", f"{base_name}_b"]
        names = [name for name in names if name in partitions and partitions[name].size]
        names = names[:1]
    else:
        names = [f"{base_name}_{slot}"]
    selected = [partitions[name] for name in names if name in partitions and partitions[name].size]
    if not selected:
        raise SuperImageError(
            f"cannot find a non-empty {base_name} logical partition for slot {slot}"
        )
    return selected


def run(command: list[str]) -> None:
    subprocess.run(command, check=True)


def prepare_avb_image(source: Path, partition: Partition, avbtool: Path, directory: Path) -> Path:
    if not source.is_file():
        raise SuperImageError(f"DLKM image not found: {source}")
    if source.stat().st_size > partition.size:
        raise SuperImageError(
            f"{source.name} is {source.stat().st_size} bytes but {partition.name} "
            f"only has {partition.size} bytes"
        )

    base_name = partition.name.removesuffix("_a").removesuffix("_b")
    prepared = directory / f"{base_name}.img"
    shutil.copyfile(source, prepared)
    if prepared.stat().st_size != partition.size:
        run(
            [
                str(avbtool),
                "resize_image",
                "--image",
                str(prepared),
                "--partition_size",
                str(partition.size),
            ]
        )
    run([str(avbtool), "verify_image", "--image", str(prepared)])
    return prepared


def file_sha256(path: Path) -> bytes:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.digest()


def patch_partition(output, prepared: Path, partition: Partition) -> None:
    with prepared.open("rb") as source:
        for extent in partition.extents:
            output.seek(extent.target_data * SECTOR_SIZE)
            remaining = extent.size
            while remaining:
                chunk = source.read(min(4 * 1024 * 1024, remaining))
                if not chunk:
                    raise SuperImageError(f"prepared image for {partition.name} is truncated")
                output.write(chunk)
                remaining -= len(chunk)
        if source.read(1):
            raise SuperImageError(f"prepared image for {partition.name} exceeds its extents")


def embedded_sha256(output, partition: Partition) -> bytes:
    digest = hashlib.sha256()
    for extent in partition.extents:
        output.seek(extent.target_data * SECTOR_SIZE)
        remaining = extent.size
        while remaining:
            chunk = output.read(min(4 * 1024 * 1024, remaining))
            if not chunk:
                raise SuperImageError(f"patched extent for {partition.name} is truncated")
            digest.update(chunk)
            remaining -= len(chunk)
    return digest.digest()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stock-super", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--system-dlkm", required=True, type=Path)
    parser.add_argument("--vendor-dlkm", required=True, type=Path)
    parser.add_argument("--avbtool", required=True, type=Path)
    parser.add_argument("--slot", choices=("auto", "a", "b", "all"), default="auto")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    stock_super = args.stock_super.resolve()
    output = args.output.resolve()
    if not stock_super.is_file():
        raise SuperImageError(f"stock super image not found: {stock_super}")
    if not args.avbtool.is_file():
        raise SuperImageError(f"avbtool not found: {args.avbtool}")
    if output == stock_super:
        raise SuperImageError("output must differ from the stock super image")

    partitions = read_metadata(stock_super)
    replacements: list[tuple[Partition, Path]] = []
    for base_name, source in (
        ("system_dlkm", args.system_dlkm.resolve()),
        ("vendor_dlkm", args.vendor_dlkm.resolve()),
    ):
        for partition in resolve_partition(partitions, base_name, args.slot):
            if not source.is_file():
                raise SuperImageError(f"DLKM image not found: {source}")
            if source.stat().st_size > partition.size:
                raise SuperImageError(
                    f"{source.name} does not fit {partition.name}: "
                    f"{source.stat().st_size} > {partition.size} bytes"
                )
            for extent in partition.extents:
                extent_end = (extent.target_data * SECTOR_SIZE) + extent.size
                if extent_end > stock_super.stat().st_size:
                    raise SuperImageError(
                        f"extent for {partition.name} ends beyond the stock super image"
                    )
            replacements.append((partition, source))
            print(
                f"{partition.name}: allocation={partition.size} image={source.stat().st_size}"
            )

    if args.dry_run:
        print("Metadata and replacement sizes are valid (dry run).")
        return 0

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_output = output.with_name(f".{output.name}.tmp.{os.getpid()}")
    try:
        run(
            [
                "cp",
                "--reflink=auto",
                "--sparse=always",
                "--",
                str(stock_super),
                str(temporary_output),
            ]
        )
        with tempfile.TemporaryDirectory(prefix="patch-super-dlkm.") as temporary_dir:
            prepared_dir = Path(temporary_dir)
            with temporary_output.open("r+b") as patched:
                for index, (partition, source) in enumerate(replacements):
                    per_partition_dir = prepared_dir / str(index)
                    per_partition_dir.mkdir()
                    prepared = prepare_avb_image(
                        source, partition, args.avbtool.resolve(), per_partition_dir
                    )
                    patch_partition(patched, prepared, partition)
                    patched.flush()
                    if embedded_sha256(patched, partition) != file_sha256(prepared):
                        raise SuperImageError(f"verification failed for {partition.name}")
                    print(f"Patched and verified {partition.name}")
                os.fsync(patched.fileno())
        os.replace(temporary_output, output)
    finally:
        temporary_output.unlink(missing_ok=True)

    print(f"Patched stock super image: {output}")
    print("NOTE: vbmeta_system must match the rebuilt DLKM descriptors, or AVB verification must be disabled.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (SuperImageError, OSError, subprocess.CalledProcessError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
