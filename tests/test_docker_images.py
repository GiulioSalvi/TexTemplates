# SPDX-FileCopyrightText: 2026 Giulio Salvi
# SPDX-License-Identifier: GPL-3.0-or-later
"""Registry retry guards must preserve published version tags."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "docker_images", Path(__file__).resolve().parents[1] / "scripts" / "docker-images.py")
images = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(images)


class PublishImagesTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.path = Path(self.scratch.name)
        self.native = {"amd64": "sha256:" + "a" * 64, "arm64": "sha256:" + "b" * 64}
        for arch, digest in self.native.items():
            (self.path / arch).write_text(digest + "\n")
        self.repositories = ["ghcr.io/giuliosalvi/tex-templates", "docker.io/giuliosalvi485/tex-templates"]
        self.labels = {"org.opencontainers.image.version": "0.1.0",
                       "org.opencontainers.image.revision": "c" * 40,
                       "org.tex-templates.library.sha256": "d" * 64,
                       "org.tex-templates.recipe.sha256": "e" * 64}
        self.tags = {}
        self.configs = {}
        self.created = []
        for repository in self.repositories:
            self.add_configs(repository, self.native)
        self.runner = patch.object(images, "docker", side_effect=self.fake_docker)
        self.runner.start()
        self.addCleanup(self.runner.stop)

    def add_configs(self, repository, native):
        for arch, digest in native.items():
            self.configs[f"{repository}@{digest}"] = {
                "os": "linux", "architecture": arch, "config": {"Labels": dict(self.labels)}}

    def index(self, native):
        return {"schemaVersion": 2, "manifests": [
            {"digest": digest, "platform": {"os": "linux", "architecture": arch}}
            for arch, digest in sorted(native.items())]}

    def fake_docker(self, *args, allow_missing=False):
        operation = args[0]
        if operation == "inspect":
            reference = args[1]
            if "--raw" in args:
                if reference not in self.tags:
                    self.assertTrue(allow_missing)
                    return None
                return json.dumps(self.tags[reference])
            return json.dumps(self.configs[reference])
        self.assertEqual(operation, "create")
        self.assertEqual(args[1], "--tag")
        reference = args[2]
        self.created.append(reference)
        if getattr(self, "fail_repository", None) and reference.startswith(self.fail_repository):
            raise images.ImageError("Simulated interrupted second registry publication")
        digest_map = {self.configs[ref]["architecture"]: ref.split("@", 1)[1] for ref in args[3:]}
        self.tags[reference] = self.index(digest_map)
        return ""

    def publish(self, **overrides):
        arguments = dict(version="0.1.0", commit="c" * 40, archive_sha256="d" * 64,
                         recipe_sha256="e" * 64, digests=self.path, images=self.repositories)
        arguments.update(overrides)
        return images.publish_images(**arguments)

    def test_publishes_both_architectures_to_both_registries(self):
        result = self.publish(summary=self.path / "summary")
        self.assertEqual(len(self.created), 2)
        self.assertEqual([record["status"] for record in result["images"]], ["published", "published"])
        self.assertIn("linux/amd64 and linux/arm64", (self.path / "summary").read_text())
        for repository in self.repositories:
            self.assertEqual(self.tags[f"{repository}:0.1.0"], self.index(self.native))

    def test_identical_published_tags_are_not_replaced_by_new_build_digests(self):
        older = {"amd64": "sha256:" + "1" * 64, "arm64": "sha256:" + "2" * 64}
        for repository in self.repositories:
            self.add_configs(repository, older)
            self.tags[f"{repository}:0.1.0"] = self.index(older)
        result = self.publish()
        self.assertEqual(self.created, [])
        self.assertEqual([record["status"] for record in result["images"]],
                         ["already published; preserved", "already published; preserved"])
        self.assertEqual(self.tags[f"{self.repositories[0]}:0.1.0"], self.index(older))

    def test_conflict_in_second_registry_prevents_mutation_of_first(self):
        hub = self.repositories[1]
        self.tags[f"{hub}:0.1.0"] = self.index(self.native)
        self.configs[f"{hub}@{self.native['arm64']}"]["config"]["Labels"]["org.tex-templates.recipe.sha256"] = "f" * 64
        with self.assertRaisesRegex(images.ImageError, "must not be replaced"):
            self.publish()
        self.assertEqual(self.created, [])

    def test_interrupted_dual_registry_publish_resumes_missing_target_only(self):
        self.fail_repository = self.repositories[1]
        with self.assertRaisesRegex(images.ImageError, "interrupted"):
            self.publish()
        self.created.clear()
        self.fail_repository = None
        self.publish()
        self.assertEqual(self.created, [f"{self.repositories[1]}:0.1.0"])

    def test_partial_retry_reuses_original_digests_even_when_rebuild_changed(self):
        original = dict(self.native)
        self.tags[f"{self.repositories[0]}:0.1.0"] = self.index(original)
        newer = {"amd64": "sha256:" + "1" * 64, "arm64": "sha256:" + "2" * 64}
        for arch, digest in newer.items():
            (self.path / arch).write_text(digest)
        for repository in self.repositories:
            self.add_configs(repository, newer)
        self.publish()
        self.assertEqual(self.tags[f"{self.repositories[1]}:0.1.0"], self.index(original))
        self.assertEqual(self.created, [f"{self.repositories[1]}:0.1.0"])

    def test_divergent_existing_registry_digests_cannot_be_silently_preserved(self):
        other = {"amd64": "sha256:" + "1" * 64, "arm64": "sha256:" + "2" * 64}
        self.tags[f"{self.repositories[0]}:0.1.0"] = self.index(self.native)
        self.add_configs(self.repositories[1], other)
        self.tags[f"{self.repositories[1]}:0.1.0"] = self.index(other)
        with self.assertRaisesRegex(images.ImageError, "different platform digests"):
            self.publish()
        self.assertEqual(self.created, [])

    def test_missing_or_malformed_tested_digest_is_rejected_before_publication(self):
        (self.path / "arm64").unlink()
        with self.assertRaisesRegex(images.ImageError, "Missing tested"):
            self.publish()
        (self.path / "arm64").write_text("not a digest")
        with self.assertRaisesRegex(images.ImageError, "Invalid tested"):
            self.publish()
        self.assertEqual(self.created, [])

    def test_unexpected_native_architecture_is_rejected_before_first_tag(self):
        self.configs[f"{self.repositories[1]}@{self.native['arm64']}"]["architecture"] = "amd64"
        with self.assertRaisesRegex(images.ImageError, "not a linux/arm64"):
            self.publish()
        self.assertEqual(self.created, [])

    def test_existing_single_platform_tag_is_not_silently_replaced(self):
        self.tags[f"{self.repositories[0]}:0.1.0"] = {"schemaVersion": 2, "config": {"digest": self.native['amd64']}}
        with self.assertRaisesRegex(images.ImageError, "exactly linux/amd64"):
            self.publish()
        self.assertEqual(self.created, [])

    def test_explicit_revision_suffix_preserves_library_version_labels(self):
        result = self.publish(image_tag="0.1.0-r1")
        self.assertEqual(result["image_tag"], "0.1.0-r1")
        self.assertEqual(self.created, [f"{repository}:0.1.0-r1" for repository in self.repositories])

    def test_invalid_tag_and_unrelated_registry_are_rejected(self):
        for tag in ("latest", "0.1.1", "0.1.0;evil", "0.1.0-r/1"):
            with self.assertRaises(images.ImageError):
                self.publish(image_tag=tag)
        with self.assertRaises(images.ImageError):
            self.publish(images=["docker.io/someone/other-image"])
        self.assertEqual(self.created, [])


class RegistryFailureTests(unittest.TestCase):
    def test_missing_manifest_can_be_treated_as_absent(self):
        failure = subprocess.CompletedProcess([], 1, "", "ERROR: manifest unknown: manifest unknown")
        with patch.object(images.subprocess, "run", return_value=failure):
            self.assertIsNone(images.docker("inspect", "docker.io/account/tex-templates:0.1.0", "--raw", allow_missing=True))

    def test_authentication_or_service_failure_cannot_be_treated_as_absent(self):
        for message in ("401 unauthorized", "403 forbidden", "503 service unavailable", "permission denied"):
            failure = subprocess.CompletedProcess([], 1, "", message)
            with patch.object(images.subprocess, "run", return_value=failure):
                with self.assertRaises(images.ImageError):
                    images.docker("inspect", "docker.io/account/tex-templates:0.1.0", "--raw", allow_missing=True)


if __name__ == "__main__":
    unittest.main()
