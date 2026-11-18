import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from contract import recipe_hash, validate_manifest, validate_proof

REV = "a" * 40
DIGEST = "sha256:" + "b" * 64
STAGING = "ttl.sh/qinglong-lazycat-abcdef123456:24h@" + DIGEST
MIRROR = "registry.lazycat.cloud/peter/qinglong:0123456789abcdef"


class AdapterContract(unittest.TestCase):
    def test_live_manifest_uses_multi_instance_with_existing_workspace(self):
        manifest = yaml.safe_load((ROOT / "lzc-manifest.yml").read_text())
        self.assertIs(manifest["application"].get("multi_instance"), True)
        validate_manifest(ROOT)

    def test_workspace_or_instance_routing_regression_blocks_packaging(self):
        for field in ("multi_instance", "routes", "binds"):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                shutil.copyfile(ROOT / "package.yml", root / "package.yml")
                manifest = yaml.safe_load((ROOT / "lzc-manifest.yml").read_text())
                if field == "multi_instance":
                    manifest["application"]["multi_instance"] = False
                elif field == "routes":
                    manifest["application"]["routes"] = [
                        "/=http://$uid.gateway.cloud.lazycat.app.qinglong.lzcapp:5700"
                    ]
                else:
                    manifest["services"]["gateway"]["binds"] = [
                        "/lzcapp/var/.ql:/ql/data"
                    ]
                (root / "lzc-manifest.yml").write_text(yaml.safe_dump(manifest))
                with self.assertRaises(ValueError):
                    validate_manifest(root)

    def test_copy_legacy_workspace_resumes_and_preserves_existing_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / ".ql"
            current = root / "work"
            current.mkdir()
            command = [
                "sh", str(ROOT / "content" / "migrate-workspace.sh"), legacy, current
            ]
            subprocess.run(command, check=True)
            self.assertEqual(list(current.iterdir()), [])

            pending = current / ".lazycat-migration-pending"
            pending.touch()
            (current / "tasks.db").write_text("partial")
            self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)
            self.assertTrue(pending.exists())

            legacy.mkdir()
            (legacy / "tasks.db").write_text("legacy")
            (legacy / ".env").write_text("legacy config")
            subprocess.run(command, check=True)
            self.assertFalse(pending.exists())
            self.assertEqual((current / "tasks.db").read_text(), "legacy")
            self.assertEqual((current / ".env").read_text(), "legacy config")

            (current / "tasks.db").write_text("updated")
            subprocess.run(command, check=True)
            self.assertEqual((current / "tasks.db").read_text(), "updated")
            self.assertEqual((legacy / "tasks.db").read_text(), "legacy")

    def test_recipe_is_stable_across_manifest_image_edit_but_binds_the_injects(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in (
                "lazycat-action.yml",
                "lzc-build.yml",
                "lzc-manifest.yml",
                "icon.png",
            ):
                shutil.copyfile(ROOT / name, root / name)
            baseline = recipe_hash(root)
            path = root / "lzc-manifest.yml"
            manifest = yaml.safe_load(path.read_text())
            manifest["services"]["gateway"]["image"] = MIRROR
            path.write_text(yaml.safe_dump(manifest))
            self.assertEqual(recipe_hash(root), baseline)
            manifest["application"]["injects"][2]["auth_required"] = False
            path.write_text(yaml.safe_dump(manifest))
            self.assertNotEqual(recipe_hash(root), baseline)

    def test_delivered_image_has_to_match_tested_runtime_and_recipe(self):
        proof = {
            "source_revision": REV,
            "source_version": "2.24.0",
            "application_version": "2.24.1",
            "staged_ref": STAGING,
            "content_sha256": DIGEST,
            "recipe": DIGEST,
            "checks": ["upgrade", "fresh-install"],
        }
        content = {
            "content_sha256": DIGEST,
            "config": {
                "config": {
                    "Labels": {
                        "org.opencontainers.image.revision": REV,
                        "org.opencontainers.image.version": "2.24.0",
                        "cloud.lazycat.qinglong.recipe": DIGEST,
                    }
                }
            },
        }
        validate_proof(proof, STAGING, MIRROR, content, DIGEST, "2.24.1")
        for fields in (
            {"source": STAGING[:-1] + "c"},
            {"mirror": MIRROR + ".example"},
            {"image": {"content_sha256": "wrong"}},
            {"recipe": "wrong"},
            {"version": "2.24.2"},
            {"proof": {**proof, "checks": ["upgrade"]}},
        ):
            args = dict(
                proof=proof,
                source=STAGING,
                mirror=MIRROR,
                image=content,
                recipe=DIGEST,
                version="2.24.1",
            )
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                validate_proof(**(args | fields))


if __name__ == "__main__":
    unittest.main()
