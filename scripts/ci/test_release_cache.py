#!/usr/bin/env python3
"""Offline regression tests for failed/partial uploads and archive restoration."""

import copy
import io
import json
import os
from pathlib import Path
import shutil
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import toolchain_cache as cache
from build_kernel import select_targets


class LocalRelease:
    """Exercise restore against real split files, without a GitHub account."""
    def __init__(self, directory, descriptor):
        self.directory = directory
        self.assets = {p["name"]: {"state": "uploaded", "size": p["size"],
                                      "digest": "sha256:" + p["sha256"]}
                       for p in descriptor["archive"]["pieces"]}

    def download(self, name, directory):
        result = Path(directory) / name
        shutil.copyfile(self.directory / name, result)
        return result


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project = {"id": "v2-test-123", "path": "kernel_platform/prebuilts/test",
                        "name": "test", "url": "https://example.com/test",
                        "revision": "a" * 40}

    def tearDown(self):
        self.temp.cleanup()

    def make_archive(self, dangerous=False):
        archive = self.root / "test.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            binary = tarfile.TarInfo("bin/compiler")
            binary.mode = 0o755
            payload = b"#!/bin/sh\necho test\n" + os.urandom(4096)
            binary.size = len(payload)
            tar.addfile(binary, io.BytesIO(payload))
            symlink = tarfile.TarInfo("bin/cc")
            symlink.type = tarfile.SYMTYPE
            symlink.linkname = "/tmp/escaped" if dangerous else "compiler"
            tar.addfile(symlink)
        descriptor = cache.split_archive(archive, self.project, self.root, chunk_bytes=127)
        return descriptor

    def test_manifest_covers_all_30_dependencies_and_separates_build_tools(self):
        manifest = Path(__file__).resolve().parents[2] / "manifests/oneplus_15.xml"
        data = cache.manifest_data(manifest)
        self.assertEqual(len(data["projects"]), 30)
        projects = {p["path"]: p for p in data["projects"]}
        self.assertNotEqual(projects["kernel_platform/prebuilts/kernel-build-tools"]["id"],
                            projects["kernel_platform/prebuilts/build-tools"]["id"])
        self.assertFalse(any(p["name"] in cache.SOURCE_NAMES for p in data["projects"]))
        self.assertEqual(len({p["id"] for p in data["projects"]}), 30)

    def test_manifest_rejects_floating_revisions_and_includes(self):
        manifest = self.root / "m.xml"
        manifest.write_text('<manifest><remote name="r" fetch="https://example.com"/>'
                            '<default remote="r" revision="main"/>'
                            '<project name="p" path="kernel_platform/prebuilts/p"/></manifest>')
        with self.assertRaisesRegex(ValueError, "immutable"):
            cache.manifest_data(manifest)
        manifest.write_text('<manifest><include name="other.xml"/></manifest>')
        with self.assertRaisesRegex(ValueError, "flattened"):
            cache.manifest_data(manifest)

    def test_multipart_roundtrip_keeps_mode_and_relative_symlink(self):
        descriptor = self.make_archive()
        self.assertGreater(len(descriptor["archive"]["pieces"]), 2)
        release = LocalRelease(self.root, descriptor)
        destination = self.root / "restored"
        cache.restore_archive(release, descriptor, destination)
        self.assertTrue(os.access(destination / "bin/compiler", os.X_OK))
        self.assertEqual(os.readlink(destination / "bin/cc"), "compiler")
        self.assertEqual((destination / "bin/cc").read_bytes(),
                         (destination / "bin/compiler").read_bytes())

    def test_first_piece_only_is_never_a_cache_hit(self):
        descriptor = self.make_archive()
        release = LocalRelease(self.root, descriptor)
        first = descriptor["archive"]["pieces"][0]["name"]
        release.assets = {first: release.assets[first]}
        with self.assertRaisesRegex(ValueError, "incomplete"):
            cache.validate_descriptor(descriptor, self.project, release.assets)

    def test_corrupt_piece_aborts_before_installing_tools(self):
        descriptor = self.make_archive()
        piece = self.root / descriptor["archive"]["pieces"][1]["name"]
        piece.write_bytes(b"x" * piece.stat().st_size)
        destination = self.root / "never-installed"
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            cache.restore_archive(LocalRelease(self.root, descriptor), descriptor, destination)
        self.assertFalse(destination.exists())

    def test_wrong_commit_and_reordered_pieces_are_rejected(self):
        descriptor = self.make_archive()
        wrong = copy.deepcopy(self.project)
        wrong["revision"] = "b" * 40
        with self.assertRaisesRegex(ValueError, "revision mismatch"):
            cache.validate_descriptor(descriptor, wrong)
        descriptor["archive"]["pieces"].reverse()
        with self.assertRaisesRegex(ValueError, "unordered"):
            cache.validate_descriptor(descriptor)

    def test_archive_cannot_install_absolute_symlinks(self):
        descriptor = self.make_archive(dangerous=True)
        destination = self.root / "never-installed"
        with self.assertRaises(tarfile.FilterError):
            cache.restore_archive(LocalRelease(self.root, descriptor), descriptor, destination)
        self.assertFalse(destination.exists())

    def test_existing_source_tree_is_not_overwritten(self):
        descriptor = self.make_archive()
        destination = self.root / "existing"
        destination.mkdir()
        (destination / "user-fix.c").write_text("keep my fix")
        with self.assertRaisesRegex(ValueError, "overwrite"):
            cache.restore_archive(LocalRelease(self.root, descriptor), descriptor, destination)
        self.assertEqual((destination / "user-fix.c").read_text(), "keep my fix")

    def test_complete_index_is_not_published_for_a_failed_project(self):
        project = self.project
        release = LocalRelease(self.root, self.make_archive())
        with patch.object(release, "read_json", side_effect=ValueError("missing receipt"), create=True):
            with self.assertRaisesRegex(ValueError, "missing receipt"):
                cache.assemble_index(release, {"projects": [project], "schema": 2})

    def test_manifest_links_are_relative_and_follow_checkout_moves(self):
        source = self.root / "kernel_platform/prebuilts/test"
        source.mkdir(parents=True)
        data = {"links": [{"project_path": "kernel_platform/prebuilts/test", "src": ".",
                           "dest": "kernel_platform/build/prebuilts/test", "kind": "linkfile"}]}
        cache.restore_links(data, self.root)
        link = self.root / "kernel_platform/build/prebuilts/test"
        self.assertFalse(os.path.isabs(os.readlink(link)))
        self.assertEqual(link.resolve(), source)

    def test_bazel_targets_include_oem_modules_but_skip_bootloader(self):
        labels = select_targets("\n".join([
            "//soc-repo:canoe_perf_abl_dist", "//soc-repo:canoe_perf_dist",
            "//soc-repo:canoe_perf_all_oplus_ddk_modules_dist",
            "//soc-repo:canoe_perf_dtc_dist", "//soc-repo:canoe_consolidate_dist",
            "//soc-repo:canoe_perf", "//soc-repo:canoe_perf_dist"]), "perf")
        self.assertEqual(labels[0], "//soc-repo:canoe_perf_dist")
        self.assertEqual(len(labels), 3)
        self.assertIn("//soc-repo:canoe_perf_all_oplus_ddk_modules_dist", labels)
        with self.assertRaisesRegex(ValueError, "did not return"):
            select_targets("//soc-repo:canoe_perf_abl_dist", "perf")


if __name__ == "__main__":
    unittest.main()
