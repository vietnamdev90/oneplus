#!/usr/bin/env python3
"""Versioned, checksummed GitHub Release cache for the OnePlus 15 manifest.

Requires Python 3.12+, git/git-lfs, gh, GNU tar and pigz on Linux.
The build/restore commands never fetch toolchains from upstream repositories.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from urllib.parse import quote, urljoin
import xml.etree.ElementTree as ET

SCHEMA = 2
CHUNK_BYTES = 1500 * 1024 * 1024
SOURCE_NAMES = {
    "android_kernel_common_oneplus_sm8850",
    "android_kernel_oneplus_sm8850",
    "android_kernel_modules_and_devicetree_oneplus_sm8850",
}


def run(*args, cwd=None, capture=False, env=None):
    return subprocess.run([str(a) for a in args], cwd=cwd, env=env, check=True,
                          text=True, stdout=subprocess.PIPE if capture else None)


def retry_run(*args, **kwargs):
    for attempt in range(3):
        try:
            return run(*args, **kwargs)
        except subprocess.CalledProcessError:
            if attempt == 2:
                raise
            print(f"Network operation failed; retry {attempt + 2}/3", flush=True)
            time.sleep(5 * (attempt + 1))


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def safe_relative(value, allow_root=False):
    p = PurePosixPath(value)
    if p.is_absolute() or ".." in p.parts or "\\" in value:
        raise ValueError(f"Unsafe relative path: {value!r}")
    if not p.parts and not allow_root:
        raise ValueError("Empty project path")
    return p.as_posix()


def manifest_data(filename):
    root = ET.parse(filename).getroot()
    allowed = {"remote", "default", "project"}
    if root.tag != "manifest" or any(e.tag not in allowed for e in root):
        raise ValueError("Use a flattened manifest containing remote/default/project only")
    remotes = {r.attrib["name"]: r for r in root.findall("remote")}
    default = root.find("default")
    defaults = default.attrib if default is not None else {}
    projects, links, seen = [], [], set()
    for p in root.findall("project"):
        name = p.attrib["name"]
        path = safe_relative(p.get("path", name), allow_root=name in SOURCE_NAMES)
        for node in p:
            if node.tag not in {"linkfile", "copyfile"}:
                raise ValueError(f"Unsupported project child: {node.tag}")
            links.append({"project_path": path, "kind": node.tag,
                          "src": safe_relative(node.attrib["src"], allow_root=True),
                          "dest": safe_relative(node.attrib["dest"])})
        if name in SOURCE_NAMES:
            continue
        if not path.startswith("kernel_platform/") or path in seen:
            raise ValueError(f"Unexpected/duplicate dependency path: {path}")
        seen.add(path)
        remote = remotes[p.get("remote", defaults.get("remote"))]
        revision = p.get("revision", remote.get("revision", defaults.get("revision", "")))
        if not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise ValueError(f"Dependency {name} must use an immutable 40-character SHA")
        fetch = remote.attrib["fetch"]
        if not fetch.startswith("https://"):
            raise ValueError(f"Only HTTPS upstream remotes are supported: {fetch}")
        url = urljoin(fetch.rstrip("/") + "/", name)
        identity = hashlib.sha256(f"{url}\n{revision}\n{path}".encode()).hexdigest()[:16]
        label = re.sub(r"[^a-zA-Z0-9_.-]+", "-", path.removeprefix("kernel_platform/"))
        projects.append({"id": f"v2-{label}-{identity}", "path": path,
                         "name": name, "url": url, "revision": revision})
    if not projects:
        raise ValueError("Manifest has no dependencies; refusing to publish an empty cache")
    digest = sha256(filename)
    return {"schema": SCHEMA, "manifest_sha256": digest,
            "index_name": f"oneplus15-{digest[:16]}-v2.json",
            "projects": projects, "links": links}


class Release:
    def __init__(self, repo, tag, create=False):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
            raise ValueError("Expected owner/repository")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", tag):
            raise ValueError("Use a release tag containing letters, digits, '.', '_' or '-'")
        self.repo, self.tag = repo, tag
        endpoint = f"repos/{repo}/releases/tags/{quote(tag, safe='')}"
        response = subprocess.run(["gh", "api", endpoint], text=True, capture_output=True)
        if response.returncode:
            if not create or "HTTP 404" not in response.stderr:
                raise RuntimeError(response.stderr.strip())
            run("gh", "release", "create", tag, "--repo", repo,
                "--target", os.environ["GITHUB_SHA"], "--latest=false",
                "--title", "OnePlus 15 build dependencies",
                "--notes", "Manifest-pinned build tools and Bazel repository cache. "
                "A v2 index is published only after all dependencies pass validation.")
            response = run("gh", "api", endpoint, capture=True)
        self.release_id = json.loads(response.stdout)["id"]
        self.refresh()

    def refresh(self):
        pages = json.loads(run("gh", "api", "--paginate", "--slurp",
                               f"repos/{self.repo}/releases/{self.release_id}/assets?per_page=100",
                               capture=True).stdout)
        self.assets = {a["name"]: a for page in pages for a in page}

    def download(self, name, folder):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
            raise ValueError(f"Unsafe asset name: {name}")
        asset = self.assets.get(name)
        if not asset or asset["state"] != "uploaded":
            raise ValueError(f"Missing release asset: {name}; run mirror-toolchains first")
        retry_run("gh", "release", "download", self.tag, "--repo", self.repo,
            "--pattern", name, "--dir", folder, "--clobber")
        path = Path(folder) / name
        if path.stat().st_size != asset["size"]:
            raise ValueError(f"Truncated release download: {name}")
        digest = asset.get("digest")
        if digest and digest.startswith("sha256:") and sha256(path) != digest[7:]:
            raise ValueError(f"GitHub asset digest mismatch: {name}")
        return path

    def read_json(self, name):
        with tempfile.TemporaryDirectory() as tmp:
            return json.loads(self.download(name, tmp).read_text())

    def upload(self, path):
        retry_run("gh", "release", "upload", self.tag, path, "--repo", self.repo, "--clobber")


def validate_descriptor(descriptor, project=None, assets=None):
    if descriptor.get("schema") != SCHEMA:
        raise ValueError("Unsupported cache descriptor schema")
    if project is not None and descriptor.get("project") != project:
        raise ValueError(f"Cache project/revision mismatch: {project['path']}")
    archive = descriptor["archive"]
    if not re.fullmatch(r"[0-9a-f]{64}", archive["sha256"]):
        raise ValueError("Invalid archive digest")
    pieces = archive["pieces"]
    if not pieces or archive["size"] <= 0:
        raise ValueError("Empty archive/piece list")
    for i, piece in enumerate(pieces):
        expected = f"{descriptor['project']['id']}.tar.gz.part-{i:03d}"
        if piece["name"] != expected or piece["size"] <= 0 or piece["size"] >= 2**31:
            raise ValueError("Invalid or unordered archive pieces")
        if not re.fullmatch(r"[0-9a-f]{64}", piece["sha256"]):
            raise ValueError("Invalid piece digest")
        if assets is not None:
            asset = assets.get(piece["name"], {})
            if asset.get("size") != piece["size"] or asset.get("state") != "uploaded":
                raise ValueError(f"Missing/incomplete piece: {piece['name']}")
            if asset.get("digest") and asset["digest"] != "sha256:" + piece["sha256"]:
                raise ValueError(f"Release piece digest differs: {piece['name']}")
    if sum(p["size"] for p in pieces) != archive["size"]:
        raise ValueError("Archive size does not match its complete piece list")


def split_archive(archive, project, folder, chunk_bytes=CHUNK_BYTES):
    if not 0 < chunk_bytes < 2**31:
        raise ValueError("Each release asset must be smaller than 2 GiB")
    pieces = []
    with Path(archive).open("rb") as source:
        i = 0
        while source.tell() < Path(archive).stat().st_size:
            path = Path(folder) / f"{project['id']}.tar.gz.part-{i:03d}"
            remaining, h = chunk_bytes, hashlib.sha256()
            with path.open("wb") as out:
                while remaining and (block := source.read(min(1024 * 1024, remaining))):
                    out.write(block)
                    h.update(block)
                    remaining -= len(block)
            pieces.append({"name": path.name, "size": path.stat().st_size,
                           "sha256": h.hexdigest()})
            i += 1
    return {"schema": SCHEMA, "project": project, "archive": {
        "format": "tar.gz", "layout": "contents", "size": Path(archive).stat().st_size,
        "sha256": sha256(archive), "pieces": pieces}}


def package_and_upload(release, project, source):
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / "payload.tar.gz"
        # No provider-specific enclosing directory; retain executables and symlinks.
        run("tar", "--sort=name", "--mtime=@0", "--owner=0", "--group=0", "--numeric-owner",
            "--exclude=.git", "-I", "pigz -n -p 2 -6", "-cf", archive, "-C", source, ".")
        descriptor = split_archive(archive, project, tmp)
        archive.unlink()
        for piece in descriptor["archive"]["pieces"]:
            release.upload(Path(tmp) / piece["name"])
            (Path(tmp) / piece["name"]).unlink()
        receipt = Path(tmp) / f"{project['id']}.json"
        write_json(receipt, descriptor)
        # The receipt is the completion marker. Upload it after EVERY piece.
        release.upload(receipt)
        return descriptor


def mirror_project(release, project):
    receipt = f"{project['id']}.json"
    if receipt in release.assets:
        try:
            validate_descriptor(release.read_json(receipt), project, release.assets)
            print(f"Complete cache already exists: {project['path']}", flush=True)
            return
        except (ValueError, KeyError, TypeError) as error:
            print(f"Repairing incomplete cache: {error}", flush=True)
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "source"
        source.mkdir()
        env = os.environ | {"GIT_LFS_SKIP_SMUDGE": "1", "GIT_TERMINAL_PROMPT": "0"}
        run("git", "init", "-q", source)
        run("git", "remote", "add", "origin", project["url"], cwd=source)
        # Fetch the SHA directly: a removed upstream branch does not change the revision.
        retry_run("git", "-c", "http.lowSpeedLimit=1024", "-c", "http.lowSpeedTime=180",
            "fetch", "--depth=1", "--no-tags", "origin", project["revision"], cwd=source, env=env)
        run("git", "checkout", "--detach", "FETCH_HEAD", cwd=source, env=env)
        actual = run("git", "rev-parse", "HEAD", cwd=source, capture=True).stdout.strip()
        if actual != project["revision"]:
            raise ValueError(f"Fetched unexpected revision for {project['path']}")
        run("git", "lfs", "install", "--local", cwd=source)
        retry_run("git", "lfs", "pull", "origin", cwd=source)
        run("git", "lfs", "fsck", cwd=source)
        run("git", "submodule", "update", "--init", "--recursive", "--depth=1", cwd=source, env=env)
        run("git", "submodule", "foreach", "--recursive",
            "git lfs install --local && git lfs pull origin && git lfs fsck", cwd=source)
        # A successful tar of Git LFS pointer files would be an unusable toolchain.
        for parent, dirs, files in os.walk(source):
            dirs[:] = [d for d in dirs if d != ".git"]
            for name in files:
                path = Path(parent) / name
                if name == ".git" or path.is_symlink() or path.stat().st_size > 1024:
                    continue
                if path.read_bytes().startswith(b"version https://git-lfs.github.com/spec/v1\n"):
                    raise ValueError(f"Unresolved Git LFS pointer: {path.relative_to(source)}")
        package_and_upload(release, project, source)


def assemble_index(release, data, bazel_id=None):
    index = {k: v for k, v in data.items() if k != "projects"}
    index["components"] = []
    for project in data["projects"]:
        descriptor = release.read_json(f"{project['id']}.json")
        validate_descriptor(descriptor, project, release.assets)
        index["components"].append(descriptor)
    index["ready"] = bool(bazel_id)
    if bazel_id:
        descriptor = release.read_json(f"{bazel_id}.json")
        validate_descriptor(descriptor, assets=release.assets)
        if descriptor["project"]["path"] != ".ci-cache/bazel-repository":
            raise ValueError("Unexpected Bazel repository cache path")
        index["bazel_cache"] = descriptor
    return index


def restore_archive(release, descriptor, destination):
    validate_descriptor(descriptor, assets=release.assets)
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise ValueError(f"Refusing to overwrite an existing directory: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent, prefix=".restore-") as tmp:
        tmp = Path(tmp)
        archive = tmp / "payload.tar.gz"
        h = hashlib.sha256()
        with archive.open("wb") as output:
            for piece in descriptor["archive"]["pieces"]:
                path = release.download(piece["name"], tmp)
                if path.stat().st_size != piece["size"] or sha256(path) != piece["sha256"]:
                    raise ValueError(f"SHA-256 mismatch: {piece['name']}")
                with path.open("rb") as inp:
                    for block in iter(lambda: inp.read(1024 * 1024), b""):
                        h.update(block)
                        output.write(block)
                path.unlink()
        if h.hexdigest() != descriptor["archive"]["sha256"]:
            raise ValueError("Reassembled archive SHA-256 mismatch")
        unpacked = tmp / "unpacked"
        unpacked.mkdir()
        with tarfile.open(archive, "r:gz") as tar:
            tar.extractall(unpacked, filter="data")
        unpacked.rename(destination)


def restore_links(data, root):
    for link in data["links"]:
        source = root / link["project_path"] / link["src"]
        destination = root / link["dest"]
        if not source.exists():
            # The supplied manifest has a stale qcom_build_extensions link.
            # Preserve that fact instead of creating a new broken link.
            print(f"Manifest link source absent in this source tree: {source}")
            continue
        if not source.resolve().is_relative_to(root):
            raise ValueError(f"Link source outside checkout: {source}")
        if destination.is_symlink() and destination.resolve() == source.resolve():
            continue
        if destination.exists() and not destination.is_symlink():
            raise ValueError(f"Manifest link would replace a real file/directory: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.is_symlink():
            destination.unlink()
        if link["kind"] == "copyfile":
            shutil.copy2(source, destination)
        else:
            destination.symlink_to(os.path.relpath(source, destination.parent))


def restore(release, data, root, local_index=None):
    root = Path(root).resolve()
    index = json.loads(Path(local_index).read_text()) if local_index else release.read_json(data["index_name"])
    if index.get("schema") != SCHEMA or index.get("manifest_sha256") != data["manifest_sha256"]:
        raise ValueError("Cache index does not match this manifest")
    if not local_index and not index.get("ready"):
        raise ValueError("Mirror has not completed Bazel dependency analysis")
    entries = index["components"]
    if len(entries) != len(data["projects"]):
        raise ValueError("Incomplete manifest cache index")
    # Verify ALL components before making any changes to the checkout.
    for descriptor, project in zip(entries, data["projects"]):
        validate_descriptor(descriptor, project, release.assets)
    if index.get("ready"):
        validate_descriptor(index["bazel_cache"], assets=release.assets)
        if index["bazel_cache"]["project"]["path"] != ".ci-cache/bazel-repository":
            raise ValueError("Invalid Bazel cache destination")
    report = {"manifest_sha256": data["manifest_sha256"], "components": []}
    for descriptor in entries:
        project = descriptor["project"]
        path = root / project["path"]
        if not path.parent.resolve().is_relative_to(root):
            raise ValueError(f"Dependency parent escapes checkout: {path}")
        prebuilt = project["path"].startswith("kernel_platform/prebuilts/")
        if not prebuilt and path.is_dir() and any(path.iterdir()):
            # Keep the user's existing fixes to external libraries/build rules.
            status = "kept-source-checkout"
        else:
            receipt = root / ".ci-cache/restored" / f"{project['id']}.json"
            if path.exists() and receipt.exists() and json.loads(receipt.read_text()) == descriptor:
                status = "already-restored"
            else:
                if path.is_dir() and not any(path.iterdir()):
                    path.rmdir()
                restore_archive(release, descriptor, path)
                write_json(receipt, descriptor)
                status = "restored-release"
        print(f"{status}: {project['path']}", flush=True)
        report["components"].append({"project": project, "status": status})
    if index.get("ready"):
        cache = root / ".ci-cache/bazel-repository"
        if not cache.exists():
            restore_archive(release, index["bazel_cache"], cache)
        else:
            raise ValueError("Use a clean checkout: Bazel repository cache already exists")
        report["bazel_cache"] = index["bazel_cache"]["project"]
    restore_links(data, root)
    write_json(root / ".ci-cache/restore-report.json", report)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["plan", "mirror", "index", "restore", "publish"])
    parser.add_argument("--manifest", default="manifests/oneplus_15.xml")
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"))
    parser.add_argument("--tag", default="toolchain-cache")
    parser.add_argument("--project-id")
    parser.add_argument("--root", default=".")
    parser.add_argument("--index", help="Local bootstrap index (mirror job only)")
    parser.add_argument("--output", default=".ci-cache/bootstrap-index.json")
    args = parser.parse_args()
    data = manifest_data(args.manifest)
    if args.command == "plan":
        Release(args.repo, args.tag, create=True)
        matrix = {"include": [{"id": p["id"], "path": p["path"]} for p in data["projects"]]}
        with open(os.environ["GITHUB_OUTPUT"], "a") as stream:
            stream.write("matrix=" + json.dumps(matrix, separators=(",", ":")) + "\n")
        print(f"Mirroring all {len(data['projects'])} manifest dependencies")
        return
    release = Release(args.repo, args.tag)
    if args.command == "mirror":
        project = next((p for p in data["projects"] if p["id"] == args.project_id), None)
        if project is None:
            raise ValueError("Project is absent from the supplied manifest")
        mirror_project(release, project)
    elif args.command == "index":
        write_json(args.output, assemble_index(release, data))
    elif args.command == "restore":
        restore(release, data, args.root, args.index)
    elif args.command == "publish":
        root = Path(args.root).resolve()
        stamp = root / ".ci-cache/bazel-warmed.json"
        warmed = json.loads(stamp.read_text())
        source_sha = run("git", "rev-parse", "HEAD", cwd=root, capture=True).stdout.strip()
        if warmed != {"source_sha": source_sha, "variants": ["perf", "consolidate"]}:
            raise ValueError("Both variants must finish dependency analysis before publishing")
        project = {"id": f"v2-bazel-cache-{data['manifest_sha256'][:16]}-{source_sha}",
                   "path": ".ci-cache/bazel-repository", "source_sha": source_sha,
                   "variants": ["perf", "consolidate"]}
        package_and_upload(release, project, root / project["path"])
        release.refresh()
        index = assemble_index(release, data, project["id"])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / data["index_name"]
            write_json(path, index)
            release.upload(path)
        print(f"READY: {args.repo} / {args.tag} / {data['index_name']}")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError, RuntimeError, tarfile.TarError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
