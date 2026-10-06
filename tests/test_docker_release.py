# SPDX-FileCopyrightText: 2026 Giulio Salvi
# SPDX-License-Identifier: GPL-3.0-or-later
"""Only verified release inputs and committed fixtures may enter Docker builds."""
import base64
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import tarfile
import tempfile
import unittest


SPEC = importlib.util.spec_from_file_location(
    "docker_release", Path(__file__).resolve().parents[1] / "scripts" / "docker-release.py")
docker_release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(docker_release)

PARENT = "Example/Templates"
CHILD = "Example/Handouts"
PARENT_COMMIT = "a" * 40
CHILD_COMMIT = "b" * 40


def git_blob(contents):
    return hashlib.sha1(f"blob {len(contents)}\0".encode() + contents).hexdigest()


class FakeGitHub:
    def __init__(self, assets):
        self.assets = assets
        self.calls = []
        self.published = {"draft": False, "prerelease": False, "tag_name": "v0.1.0"}
        self.child_commit = CHILD_COMMIT
        self.fixture_files = {
            "usage_example/defaults.yaml": b"input-file: '${.}/example.md'\n",
            "usage_example/example.md": b"# Example\n![](usage_example/assets/figure.png)\n",
            "usage_example/assets/figure.png": b"\x89PNG\r\n\x1a\nsynthetic-test-fixture",
        }
        self.tree = {
            "truncated": False,
            "tree": [{"path": name, "type": "blob", "mode": "100644", "sha": git_blob(contents)}
                     for name, contents in self.fixture_files.items()] + [
                         {"path": "README.md", "type": "blob", "mode": "100644", "sha": "c" * 40},
                         {"path": "scripts/private-tool.sh", "type": "blob", "mode": "100755", "sha": "d" * 40},
                     ],
        }
        self.blobs = {git_blob(contents): {"encoding": "base64", "content": base64.b64encode(contents).decode()}
                      for contents in self.fixture_files.values()}

    def api(self, endpoint):
        self.calls.append(("api", endpoint))
        if endpoint == f"repos/{PARENT}/releases/tags/v0.1.0":
            return copy.deepcopy(self.published)
        if endpoint == f"repos/{CHILD}/git/trees/{CHILD_COMMIT}?recursive=1":
            return copy.deepcopy(self.tree)
        prefix = f"repos/{CHILD}/git/blobs/"
        if endpoint.startswith(prefix):
            return copy.deepcopy(self.blobs[endpoint.removeprefix(prefix)])
        raise AssertionError(f"Unexpected network request: {endpoint}")

    def tag_commit(self, repository, tag):
        self.calls.append(("tag", repository, tag))
        if repository == PARENT and tag == "v0.1.0":
            return PARENT_COMMIT
        if repository == CHILD and tag == "v0.1.0":
            return self.child_commit
        raise AssertionError((repository, tag))

    def download(self, repository, tag, name, destination):
        self.calls.append(("download", repository, tag, name))
        if repository != PARENT or tag != "v0.1.0":
            raise AssertionError((repository, tag))
        destination.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.assets / name, destination / name)


class DockerReleaseTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.assets = self.root / "assets"
        self.assets.mkdir()
        self.tree = self.root / "source" / "tex-templates"
        (self.tree / "bin").mkdir(parents=True)
        for name in ("generate-pdf.sh", "template-list.sh", "template-use.sh"):
            command = self.tree / "bin" / name
            command.write_text("#!/bin/sh\nexit 0\n")
            command.chmod(0o755)
        (self.tree / "README.md").write_text("Runtime library\n")
        (self.tree / "LICENSE").write_text("GPL-3.0-or-later\n")
        template = self.tree / "templates" / "handouts"
        template.mkdir(parents=True)
        for name, content in {
            "template.tex": "\\documentclass{book}\n",
            "README.md": "Template runtime\n",
            "LICENSE": "GPL-3.0-or-later\n",
            "defaults.yaml": "pdf-engine: lualatex\n",
            "requirements.txt": "fontspec\nlatex-bin\n",
        }.items():
            (template / name).write_text(content)
        self.record = {"id": "handouts", "repository": CHILD, "tag": "v0.1.0",
                       "commit": CHILD_COMMIT, "sha256": "e" * 64,
                       "archive": "handouts-0.1.0.tar.gz"}
        self.lock = {"schema": 1, "library": "tex-templates", "version": "0.1.0",
                     "commit": PARENT_COMMIT, "preview": False, "fingerprint": "f" * 64,
                     "templates": [self.record]}
        docker_release.release.aggregate_requirements(self.tree)
        self.write_assets()
        self.client = FakeGitHub(self.assets)
        self.recipe = self.root / "docker"
        self.recipe.mkdir()
        for name, contents in {"Dockerfile": "FROM synthetic:test\nCOPY library/ /opt/templates/\n",
                               "install-texlive.sh": "#!/bin/bash\nexit 0\n",
                               ".dockerignore": "/fixtures/\n"}.items():
            (self.recipe / name).write_text(contents)
        (self.recipe / "install-texlive.sh").chmod(0o755)

    def write_assets(self, embedded_lock=None, archive_entry=None):
        lock_bytes = docker_release.release.json_bytes(self.lock)
        (self.assets / "templates.lock.json").write_bytes(lock_bytes)
        (self.tree / "templates.lock.json").write_bytes(
            docker_release.release.json_bytes(embedded_lock or self.lock))
        archive = self.assets / "tex-templates-0.1.0.tar.gz"
        with tarfile.open(archive, "w:gz") as output:
            output.add(self.tree, arcname="tex-templates")
            if archive_entry:
                entry = tarfile.TarInfo(archive_entry)
                entry.size = 4
                output.addfile(entry, io.BytesIO(b"evil"))
        self.write_checksums()

    def write_checksums(self):
        names = ("tex-templates-0.1.0.tar.gz", "templates.lock.json")
        (self.assets / "SHA256SUMS").write_text("".join(
            f"{docker_release.release.file_hash(self.assets / name)}  {name}\n" for name in names))

    def prepare(self, output=None, github_output=None):
        return docker_release.prepare("v0.1.0", output or self.root / "context", PARENT,
                                      github_output, self.client, self.recipe)

    def test_prepares_curated_archive_and_only_exact_committed_example_files(self):
        github_output = self.root / "github-output"
        metadata = self.prepare(github_output=github_output)
        output = self.root / "context"
        self.assertEqual(metadata["commit"], PARENT_COMMIT)
        self.assertEqual(metadata["archive_sha256"],
                         docker_release.release.file_hash(self.assets / "tex-templates-0.1.0.tar.gz"))
        self.assertEqual(metadata["recipe_sha256"], docker_release.recipe_hash(self.recipe))
        self.assertEqual(json.loads((output / "metadata.json").read_text()), metadata)
        self.assertIn(f"commit={PARENT_COMMIT}\n", github_output.read_text())
        self.assertTrue((output / "library/bin/generate-pdf.sh").stat().st_mode & 0o111)
        self.assertEqual((output / "fixtures/handouts/usage_example/assets/figure.png").read_bytes(),
                         self.client.fixture_files["usage_example/assets/figure.png"])
        self.assertFalse((output / "library/templates/handouts/usage_example").exists())
        self.assertFalse((output / "fixtures/handouts/README.md").exists())
        self.assertFalse((output / "fixtures/handouts/scripts").exists())
        self.assertIn(("api", f"repos/{CHILD}/git/trees/{CHILD_COMMIT}?recursive=1"), self.client.calls)
        self.assertFalse(any("tarball" in str(call) for call in self.client.calls))
        self.assertFalse(list(self.root.glob("docker-release-*")))

    def test_checksum_failure_does_not_leave_an_output_context(self):
        (self.assets / "tex-templates-0.1.0.tar.gz").write_bytes(b"altered asset")
        with self.assertRaisesRegex(docker_release.release.ReleaseError, "checksum mismatch"):
            self.prepare()
        self.assertFalse((self.root / "context").exists())
        self.assertFalse(list(self.root.glob("docker-release-*")))

    def test_checksum_set_must_cover_exactly_archive_and_lock(self):
        checksums = self.assets / "SHA256SUMS"
        checksums.write_text(checksums.read_text().splitlines()[0] + "\n")
        with self.assertRaisesRegex(docker_release.release.ReleaseError, "must cover"):
            self.prepare()

    def test_new_runtime_archive_preserves_shared_filters(self):
        (self.tree / "filters").mkdir()
        (self.tree / "filters" / "include.lua").write_text("-- Shared Lua filter\n")
        self.write_assets()
        output = self.root / "output"
        docker_release.prepare("0.1.0", output, PARENT, client=self.client, docker_dir=self.recipe)
        self.assertEqual((output / "library/filters/include.lua").read_text(), "-- Shared Lua filter\n")

    def test_partial_shared_filter_runtime_is_rejected(self):
        (self.tree / "filters").mkdir()
        (self.tree / "filters" / "unrelated.lua").write_text("-- Wrong runtime\n")
        self.write_assets()
        with self.assertRaisesRegex(docker_release.release.ReleaseError, "missing filters/include.lua"):
            self.prepare()

    def test_numeric_preview_or_wrong_library_commit_cannot_be_published(self):
        for field, value in (("preview", 0), ("preview", True), ("commit", "c" * 40), ("schema", True)):
            with self.subTest(field=field, value=value):
                changed = copy.deepcopy(self.lock)
                changed[field] = value
                with self.assertRaisesRegex(docker_release.release.ReleaseError, "does not match"):
                    docker_release.validate_lock(changed, "0.1.0", PARENT_COMMIT)

    def test_embedded_lock_must_equal_the_verified_published_lock(self):
        changed = copy.deepcopy(self.lock)
        changed["fingerprint"] = "1" * 64
        self.write_assets(embedded_lock=changed)
        with self.assertRaisesRegex(docker_release.release.ReleaseError, "locks differ"):
            self.prepare()

    def test_unsafe_archive_path_is_rejected_before_output_creation(self):
        self.write_assets(archive_entry="tex-templates/../escape")
        with self.assertRaisesRegex(docker_release.release.ReleaseError, "Unsafe archive path"):
            self.prepare()
        self.assertFalse((self.root / "escape").exists())
        self.assertFalse((self.root / "context").exists())

    def test_source_material_cannot_leak_into_the_runtime_archive(self):
        (self.tree / "scripts").mkdir()
        (self.tree / "scripts/tool.py").write_text("development tool\n")
        self.write_assets()
        with self.assertRaisesRegex(docker_release.release.ReleaseError, "unexpected source material"):
            self.prepare()

    def test_examples_cannot_leak_into_an_installed_template(self):
        example = self.tree / "templates/handouts/usage_example"
        example.mkdir()
        (example / "example.md").write_text("source only\n")
        self.write_assets()
        with self.assertRaisesRegex(docker_release.release.ReleaseError, "excluded source material"):
            self.prepare()

    def test_aggregated_requirements_cannot_omit_a_template_dependency(self):
        (self.tree / "requirements.txt").write_text("# Combined TeX Live / tlmgr package names.\nlatex-bin\n")
        self.write_assets()
        with self.assertRaisesRegex(docker_release.release.ReleaseError, "Combined requirements differ"):
            self.prepare()

    def test_destination_must_be_empty_and_existing_files_are_preserved(self):
        output = self.root / "context"
        output.mkdir()
        (output / "keep").write_text("user data")
        with self.assertRaisesRegex(docker_release.release.ReleaseError, "must be empty"):
            self.prepare(output)
        self.assertEqual((output / "keep").read_text(), "user data")
        self.assertEqual(self.client.calls, [])

    def test_destination_cannot_replace_even_a_dangling_symlink(self):
        output = self.root / "context"
        output.symlink_to(self.root / "missing-user-directory", target_is_directory=True)
        with self.assertRaisesRegex(docker_release.release.ReleaseError, "must be empty"):
            self.prepare(output)
        self.assertTrue(output.is_symlink())
        self.assertEqual(self.client.calls, [])

    def test_draft_or_prerelease_cannot_be_a_docker_source(self):
        for key in ("draft", "prerelease"):
            with self.subTest(key=key):
                self.client.published[key] = True
                with self.assertRaisesRegex(docker_release.release.ReleaseError, "published stable"):
                    self.prepare()
                self.client.published[key] = False

    def test_changed_child_tag_cannot_supply_a_fixture_for_an_old_lock(self):
        self.client.child_commit = "c" * 40
        with self.assertRaisesRegex(docker_release.release.ReleaseError, "no longer matches"):
            self.prepare()
        self.assertFalse(any("git/trees" in str(call) for call in self.client.calls))

    def test_blob_contents_must_match_the_committed_git_hash(self):
        sha = git_blob(self.client.fixture_files["usage_example/example.md"])
        self.client.blobs[sha]["content"] = base64.b64encode(b"replaced document").decode()
        with self.assertRaisesRegex(docker_release.release.ReleaseError, "committed Git blob"):
            self.prepare()
        self.assertFalse((self.root / "context").exists())

    def test_symlinks_traversal_and_truncated_fixture_trees_are_rejected(self):
        original = copy.deepcopy(self.client.tree)
        for path, mode in (("usage_example/link", "120000"), ("usage_example/../outside", "100644")):
            with self.subTest(path=path):
                self.client.tree = copy.deepcopy(original)
                self.client.tree["tree"].insert(0, {"path": path, "type": "blob", "mode": mode,
                                                 "sha": "c" * 40})
                with self.assertRaisesRegex(docker_release.release.ReleaseError, "Unsafe file"):
                    self.prepare()
        self.client.tree = copy.deepcopy(original)
        self.client.tree["truncated"] = True
        with self.assertRaisesRegex(docker_release.release.ReleaseError, "complete pinned"):
            self.prepare()

    def test_recipe_changes_have_a_new_hash(self):
        initial = docker_release.recipe_hash(self.recipe)
        (self.recipe / "install-texlive.sh").write_text("#!/bin/bash\nexit 1\n")
        self.assertNotEqual(docker_release.recipe_hash(self.recipe), initial)


if __name__ == "__main__":
    unittest.main()
