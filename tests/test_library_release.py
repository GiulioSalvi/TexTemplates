# TeX Templates Library
# Copyright (C) 2026 Giulio Salvi
#
# This is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This software is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.
#
# SPDX-FileCopyrightText: 2026 Giulio Salvi
# SPDX-License-Identifier: GPL-3.0-or-later

import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "library-release.py"
spec = importlib.util.spec_from_file_location("library_release", SCRIPT)
release = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = release
spec.loader.exec_module(release)


def command(repo, *args):
    return release.git(repo, *args)


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="library-release-tests-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.parent = self.root / "parent with spaces"
        self.child = self.parent / "templates" / "handouts"
        self.child.mkdir(parents=True)
        self.initialize(self.child)
        files = {
            "template.tex": "template source\n",
            "defaults.yaml": "pdf-engine: lualatex\n",
            "environment.sh": "#!/bin/sh\n",
            "README.md": "Template README\n",
            "LICENSE": "GPL-3.0-or-later\n",
            "requirements.txt": "# tlmgr names\nfontspec\nunicode-math\nfontspec\n",
            "style/sample.tex": "Style source\n",
            "usage_example/example.md": "Excluded fixture\n",
            ".gitattributes": "\n".join(f"/{name} export-ignore" for name in
                (".gitattributes", ".gitignore", ".github", "scripts", "tests", "usage_example")) + "\n",
        }
        for name, content in files.items():
            path = self.child / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        command(self.child, "add", ".")
        command(self.child, "commit", "-m", "First template")
        command(self.child, "tag", "v1.0.0")
        self.commit = command(self.child, "rev-parse", "HEAD")
        self.initialize(self.parent)
        (self.parent / "bin").mkdir()
        (self.parent / "bin" / "generate-pdf.sh").write_text("#!/bin/sh\nexit 0\n")
        (self.parent / "bin" / "generate-pdf.sh").chmod(0o755)
        (self.parent / "README.md").write_text("Library README\n")
        (self.parent / "LICENSE").write_text("GPL-3.0-or-later\n")
        shutil.copy2(SCRIPT.parents[1] / ".gitattributes", self.parent / ".gitattributes")
        command(self.parent, "submodule", "add", "https://github.com/TestOwner/Handouts.git", "templates/handouts")
        command(self.parent, "add", ".")
        command(self.parent, "commit", "-m", "First library")
        self.assets = {}
        self.tags = {}
        self.child_releases = []
        self.parent_releases = []
        self.add_release("v1.0.0", self.commit)
        outer = self

        class FakeGitHub(release.GitHub):
            def releases(self, repository, include_drafts=False):
                values = outer.child_releases if repository == "TestOwner/Handouts" else outer.parent_releases
                return [item for item in values if include_drafts or not item["draft"]]

            def tag_commit(self, repository, tag):
                return outer.tags[(repository, tag)]

            def status(self, endpoint):
                parts = endpoint.split("/")
                repository = "/".join(parts[1:3])
                tag = parts[-1]
                if "/releases/tags/" in endpoint:
                    return next((item for item in self.releases(repository, include_drafts=True)
                                 if item["tag_name"] == tag), None)
                if "/git/ref/tags/" in endpoint:
                    commit = outer.tags.get((repository, tag))
                    return {"object": {"type": "commit", "sha": commit}} if commit else None
                raise AssertionError(endpoint)

            def download(self, repository, tag, name, destination):
                path = outer.assets[(repository, tag, name)]
                destination.mkdir(parents=True, exist_ok=True)
                target = destination / name
                shutil.copy2(path, target)
                return target

        self.client_patch = patch.object(release, "GitHub", FakeGitHub)
        self.client_patch.start()
        self.addCleanup(self.client_patch.stop)

    def initialize(self, repo):
        command(repo, "init", "-b", "main")
        command(repo, "config", "user.name", "Fixture author")
        command(repo, "config", "user.email", "fixture@example.invalid")
        command(repo, "config", "commit.gpgsign", "false")
        command(repo, "config", "core.hooksPath", "/dev/null")

    def add_release(self, tag, commit, archive_bytes=None):
        folder = self.root / "assets" / tag
        folder.mkdir(parents=True)
        stem = f"handouts-{tag[1:]}"
        archive = folder / f"{stem}.tar.gz"
        if archive_bytes is None:
            archive_bytes = release.git(self.child, "archive", "--format=tar.gz", "--prefix=handouts/", commit, binary=True)
        archive.write_bytes(archive_bytes)
        metadata = folder / f"{stem}.json"
        metadata.write_bytes(release.json_bytes({
            "schema": 1, "id": "handouts", "repository": "TestOwner/Handouts", "tag": tag,
            "commit": commit, "archive": archive.name, "sha256": release.file_hash(archive),
        }))
        sums = folder / "SHA256SUMS"
        sums.write_text(f"{release.file_hash(archive)}  {archive.name}\n{release.file_hash(metadata)}  {metadata.name}\n")
        for path in (archive, metadata, sums):
            self.assets[("TestOwner/Handouts", tag, path.name)] = path
        self.tags[("TestOwner/Handouts", tag)] = commit
        value = {"tag_name": tag, "draft": False, "prerelease": False,
                 "assets": [{"name": path.name} for path in (archive, metadata, sums)]}
        self.child_releases.append(value)
        return archive

    def event(self, tag, commit, **extra):
        path = self.root / "event.json"
        path.write_text(json.dumps({"client_payload": {"id": "handouts", "tag": tag,
                               "commit": commit, **extra}}))
        return path

    def add_parent_release(self, lock, draft=False, include_lock=True):
        tag = f"v{lock['version']}"
        value = {"tag_name": tag, "draft": draft, "prerelease": False,
                 "target_commitish": lock["commit"], "assets": []}
        if include_lock:
            path = self.root / f"parent-{tag}.json"
            path.write_bytes(release.json_bytes(lock))
            self.assets[("TestOwner/Library", tag, "templates.lock.json")] = path
            value["assets"].append({"name": "templates.lock.json"})
            if not draft:
                value["assets"].extend({"name": name} for name in
                    (f"tex-templates-{lock['version']}.tar.gz", "SHA256SUMS"))
        self.parent_releases.append(value)

    def writer_release(self, tag, unrelated=False):
        writer = self.root / "template writer"
        subprocess.run(["git", "clone", str(self.child), str(writer)], check=True, capture_output=True)
        command(writer, "config", "user.name", "Fixture author")
        command(writer, "config", "user.email", "fixture@example.invalid")
        command(writer, "config", "commit.gpgsign", "false")
        command(writer, "config", "core.hooksPath", "/dev/null")
        if unrelated:
            command(writer, "checkout", "--orphan", "unrelated")
        (writer / "README.md").write_text("New release README\n")
        command(writer, "add", ".")
        command(writer, "commit", "-m", "Next template")
        command(writer, "tag", tag)
        commit = command(writer, "rev-parse", "HEAD")
        archive_bytes = release.git(writer, "archive", "--format=tar.gz", "--prefix=handouts/", commit, binary=True)
        self.add_release(tag, commit, archive_bytes)
        original_git = release.git

        def local_fetch(repo, *args, **kwargs):
            if args and args[0] == "fetch":
                args = tuple(str(writer) if value == "https://github.com/TestOwner/Handouts.git" else value for value in args)
            return original_git(repo, *args, **kwargs)

        return commit, patch.object(release, "git", local_fetch)

    def test_versions_and_registered_identity(self):
        self.assertEqual(release.next_version(self.parent, None), "0.1.0")
        command(self.parent, "tag", "v0.4.8")
        self.assertEqual(release.next_version(self.parent, None), "0.4.9")
        self.assertEqual(release.next_version(self.parent, "1.0.0"), "1.0.0")
        for value in ("0.4.8", "01.0.0", "../1.0.0", "1.0"):
            with self.assertRaises(release.ReleaseError):
                release.next_version(self.parent, value)
        selected = release.templates(self.parent)
        self.assertEqual(selected[0].repository, "TestOwner/Handouts")
        self.assertEqual(selected[0].commit, self.commit)

    def test_archive_is_deterministic_complete_and_curated(self):
        first = release.assemble(self.parent, self.root / "one", "0.1.0")
        second = release.assemble(self.parent, self.root / "two", "0.1.0")
        self.assertEqual(Path(first["package_path"]).read_bytes(), Path(second["package_path"]).read_bytes())
        unpacked = self.root / "unpacked"
        release.safe_extract(Path(first["package_path"]), unpacked)
        tree = unpacked / "tex-templates"
        self.assertTrue((tree / "bin/generate-pdf.sh").is_file())
        self.assertTrue((tree / "templates/handouts/template.tex").is_file())
        self.assertFalse((tree / "templates/handouts/usage_example").exists())
        self.assertFalse((tree / ".gitattributes").exists())
        self.assertEqual((tree / "requirements.txt").read_text().splitlines()[1:], ["fontspec", "unicode-math"])
        lock = json.loads((tree / "templates.lock.json").read_text())
        self.assertEqual(lock["templates"][0]["tag"], "v1.0.0")
        self.assertFalse(lock["preview"])

    def test_preview_requires_no_release_assets(self):
        self.child_releases.clear()
        result = release.assemble(self.parent, self.root / "preview", "0.0.0", preview=True)
        self.assertTrue(json.loads(Path(result["lock_path"]).read_text())["preview"])

    def test_noop_dispatch_still_verifies_payload(self):
        result = release.update(self.parent, self.event("v1.0.0", self.commit, url="https://evil.invalid"))
        self.assertFalse(result["changed"])
        with self.assertRaises(release.ReleaseError):
            release.update(self.parent, self.event("v1.0.0", "0" * 40))
        self.assertEqual(release.templates(self.parent)[0].commit, self.commit)

    def test_forward_update_retry_and_stale_notification(self):
        commit, fetch_patch = self.writer_release("v1.0.1")
        with fetch_patch:
            updated = release.update(self.parent, self.event("v1.0.1", commit))
            self.assertTrue(updated["changed"])
            command(self.parent, "commit", "-m", "Update selected template")
            self.assertEqual(release.templates(self.parent)[0].commit, commit)
            self.assertFalse(release.update(self.parent, self.event("v1.0.1", commit))["changed"])
            stale = release.update(self.parent, self.event("v1.0.0", self.commit))
            self.assertTrue(stale["ignored"])
            self.assertFalse(stale["changed"])
        self.assertEqual(release.templates(self.parent)[0].commit, commit)
        self.assertFalse(command(self.parent, "status", "--porcelain", "--untracked-files=no"))

    def test_forward_non_descendant_is_rejected_without_gitlink_change(self):
        commit, fetch_patch = self.writer_release("v2.0.0", unrelated=True)
        with fetch_patch, self.assertRaisesRegex(release.ReleaseError, "non-descendant"):
            release.update(self.parent, self.event("v2.0.0", commit))
        self.assertEqual(release.templates(self.parent)[0].commit, self.commit)
        self.assertFalse(command(self.parent, "status", "--porcelain", "--untracked-files=no"))

    def test_descendant_version_downgrade_is_rejected(self):
        commit, fetch_patch = self.writer_release("v0.9.0")
        with fetch_patch, self.assertRaisesRegex(release.ReleaseError, "version downgrade"):
            release.update(self.parent, self.event("v0.9.0", commit))
        self.assertEqual(release.templates(self.parent)[0].commit, self.commit)

    def test_corrupt_archive_and_metadata_are_rejected(self):
        archive = self.assets[("TestOwner/Handouts", "v1.0.0", "handouts-1.0.0.tar.gz")]
        archive.write_bytes(archive.read_bytes() + b"corruption")
        with self.assertRaisesRegex(release.ReleaseError, "SHA256 mismatch"):
            release.assemble(self.parent, self.root / "bad", "0.1.0")

    def test_missing_release_is_actionable(self):
        self.child_releases.clear()
        with self.assertRaisesRegex(release.ReleaseError, "Publish a version tag"):
            release.assemble(self.parent, self.root / "not-ready", "0.1.0")

    def test_selected_release_identity_changes_fingerprint(self):
        first = release.assemble(self.parent, self.root / "old", "0.1.0")
        original = self.assets[("TestOwner/Handouts", "v1.0.0", "handouts-1.0.0.tar.gz")].read_bytes()
        self.add_release("v1.0.1", self.commit, original)
        second = release.assemble(self.parent, self.root / "new", "0.1.1")
        self.assertNotEqual(first["fingerprint"], second["fingerprint"])

    def test_published_collection_and_drafts(self):
        result = release.assemble(self.parent, self.root / "release", "0.1.0")
        lock = json.loads(Path(result["lock_path"]).read_text())
        self.add_parent_release(lock, draft=True)
        with patch.dict(os.environ, {"GITHUB_REPOSITORY": "TestOwner/Library"}):
            pending = release.published(self.parent, Path(result["lock_path"]))
            self.assertFalse(pending["already_published"])
            self.assertEqual(pending["draft_version"], "0.1.0")
            self.parent_releases[0]["draft"] = False
            self.parent_releases[0]["assets"].extend({"name": name} for name in
                ("tex-templates-0.1.0.tar.gz", "SHA256SUMS"))
            self.assertTrue(release.published(self.parent, Path(result["lock_path"]))["already_published"])

    def test_http_status_errors_fail_closed(self):
        for status, output, code, expected in (
            (404, "HTTP/2.0 404 Not Found\ncontent-type: application/json\n\n{}", 1, None),
            (200, "HTTP/2.0 200 OK\ncontent-type: application/json\n\n{}", 0, {}),
            (403, "HTTP/2.0 403 Forbidden\n\n{}", 1, "error"),
            (0, "", 1, "error"),
        ):
            with self.subTest(status=status), patch.object(subprocess, "run", return_value=
                    subprocess.CompletedProcess([], code, stdout=output, stderr="test HTTP failure")):
                # Call the original method, bypassing the fixture's fake HTTP client.
                original_method = release.GitHub.__mro__[1].status
                if expected == "error":
                    with self.assertRaises(release.ReleaseError):
                        original_method(release.GitHub(), "repos/TestOwner/Library/releases/tags/v0.1.0")
                else:
                    self.assertEqual(original_method(release.GitHub(), "repos/TestOwner/Library/releases/tags/v0.1.0"), expected)

    def test_publish_only_clobbers_the_reserved_matching_draft(self):
        result = release.assemble(self.parent, self.root / "release", "0.1.0")
        lock_path = Path(result["lock_path"])
        lock = json.loads(lock_path.read_text())
        self.add_parent_release(lock, draft=True)
        notes = self.root / "notes.md"
        notes.write_text("Release notes\n")
        with patch.dict(os.environ, {"GITHUB_REPOSITORY": "TestOwner/Library"}), patch.object(release, "run") as invoke:
            with self.assertRaisesRegex(release.ReleaseError, "unrelated"):
                release.publish(self.parent, lock_path, Path(result["package_path"]),
                                Path(result["checksums_path"]), notes, "")
            invoke.assert_not_called()
            release.publish(self.parent, lock_path, Path(result["package_path"]),
                            Path(result["checksums_path"]), notes, "v0.1.0")
            calls = [call.args for call in invoke.call_args_list]
            self.assertTrue(any("--clobber" in call for call in calls))
            self.assertFalse(any("create" in call for call in calls))
            self.assertTrue(any("--draft=false" in call for call in calls))

    def test_retargeted_draft_is_rejected(self):
        result = release.assemble(self.parent, self.root / "release", "0.1.0")
        lock = json.loads(Path(result["lock_path"]).read_text())
        self.add_parent_release(lock, draft=True)
        self.parent_releases[0]["target_commitish"] = "0" * 40
        with patch.dict(os.environ, {"GITHUB_REPOSITORY": "TestOwner/Library"}), self.assertRaisesRegex(release.ReleaseError, "Draft source"):
            release.published(self.parent, Path(result["lock_path"]))

    def test_draft_before_asset_upload_is_resumable(self):
        result = release.assemble(self.parent, self.root / "release", "0.1.0")
        lock = json.loads(Path(result["lock_path"]).read_text())
        self.add_parent_release(lock, draft=True, include_lock=False)
        with patch.dict(os.environ, {"GITHUB_REPOSITORY": "TestOwner/Library"}):
            pending = release.published(self.parent, Path(result["lock_path"]))
        self.assertEqual(pending["draft_commit"], lock["commit"])

    def test_resume_uses_original_source_commit(self):
        result = release.assemble(self.parent, self.root / "release", "0.1.0")
        old = json.loads(Path(result["lock_path"]).read_text())
        (self.parent / "source-only.txt").write_text("Excluded from runtime allowlist\n")
        command(self.parent, "add", "source-only.txt")
        command(self.parent, "commit", "-m", "Source-only maintenance")
        resumed = release.assemble(self.parent, self.root / "resume", "0.1.0", source_commit=old["commit"])
        self.assertEqual(Path(result["package_path"]).read_bytes(), Path(resumed["package_path"]).read_bytes())

    def test_unsafe_tar_entries_are_rejected(self):
        for name, kind, link in (("../escape", "file", ""), ("/absolute", "file", ""),
                                 ("other/template.tex", "file", ""),
                                 ("handouts/template.tex", "symlink", "a/../outside.tex"),
                                 ("handouts/template.tex", "hardlink", "../escape")):
            with self.subTest(name=name, kind=kind):
                data = io.BytesIO()
                with tarfile.open(fileobj=data, mode="w") as archive:
                    item = tarfile.TarInfo(name)
                    if kind == "symlink":
                        item.type, item.linkname = tarfile.SYMTYPE, link
                    elif kind == "hardlink":
                        item.type, item.linkname = tarfile.LNKTYPE, link
                    archive.addfile(item)
                data.seek(0)
                with self.assertRaises(release.ReleaseError):
                    release.safe_extract(data, self.root / "malicious", "handouts")


if __name__ == "__main__":
    unittest.main()
