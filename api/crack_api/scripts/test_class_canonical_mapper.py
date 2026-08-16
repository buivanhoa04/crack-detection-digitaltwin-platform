import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from class_canonical_mapper import (  # noqa: E402
    CANONICAL_BRIDGE_NAMES,
    ClassMappingError,
    ModelClassRemapper,
)


class ModelClassRemapperTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.model_path = self.root / "model.pt"
        self.model_path.write_bytes(b"deterministic-test-model")
        self.model_hash = hashlib.sha256(self.model_path.read_bytes()).hexdigest().upper()
        self.raw_names = dict(CANONICAL_BRIDGE_NAMES)

    def tearDown(self):
        self.temp_dir.cleanup()

    def write_config(self, model_config):
        path = self.root / "class_mapping_config.json"
        path.write_text(
            json.dumps({"schema_version": 1, "models": {"bridge": model_config}}),
            encoding="utf-8",
        )
        return path

    def approved_config(self):
        permutation = {class_id: class_id for class_id in CANONICAL_BRIDGE_NAMES}
        permutation[0], permutation[3] = permutation[3], permutation[0]
        return {
            "enabled": True,
            "status": "approved",
            "model_sha256": self.model_hash,
            "raw_names": {str(key): value for key, value in self.raw_names.items()},
            "class_permutation_map": {
                str(raw_id): {
                    "canonical_id": canonical_id,
                    "canonical_name": CANONICAL_BRIDGE_NAMES[canonical_id],
                }
                for raw_id, canonical_id in permutation.items()
            },
        }

    def create_mapper(self, config):
        return ModelClassRemapper(
            model_type="bridge",
            model_path=self.model_path,
            raw_names=self.raw_names,
            config_path=self.write_config(config),
        )

    def test_disabled_mapping_preserves_raw_output(self):
        mapper = self.create_mapper({"enabled": False, "status": "audit_required"})
        result = mapper.remap_detection(9, "Guardrail Damaged")

        self.assertFalse(result["class_mapping_applied"])
        self.assertEqual(result["raw_class_id"], 9)
        self.assertEqual(result["class_id"], 9)
        self.assertEqual(result["class_name"], "Guardrail Damaged")

    def test_approved_mapping_is_applied_and_raw_output_is_preserved(self):
        mapper = self.create_mapper(self.approved_config())
        result = mapper.remap_detection(3, "Spalling")

        self.assertTrue(result["class_mapping_applied"])
        self.assertEqual(result["raw_class_id"], 3)
        self.assertEqual(result["raw_class_name"], "Spalling")
        self.assertEqual(result["class_id"], 0)
        self.assertEqual(result["class_name"], "Crack")

    def test_enabled_mapping_rejects_wrong_model_hash(self):
        config = self.approved_config()
        config["model_sha256"] = "0" * 64

        with self.assertRaisesRegex(ClassMappingError, "SHA-256 mismatch"):
            self.create_mapper(config)

    def test_enabled_mapping_rejects_incomplete_permutation(self):
        config = self.approved_config()
        del config["class_permutation_map"]["10"]

        with self.assertRaisesRegex(ClassMappingError, "cover every raw ID"):
            self.create_mapper(config)

    def test_enabled_mapping_rejects_duplicate_canonical_class(self):
        config = self.approved_config()
        config["class_permutation_map"]["1"] = dict(
            config["class_permutation_map"]["0"]
        )

        with self.assertRaisesRegex(ClassMappingError, "one-to-one permutation"):
            self.create_mapper(config)

    def test_production_bridge_mapping_matches_approved_roboflow_order(self):
        project_root = Path(__file__).resolve().parents[1]
        model_path = project_root / "weights" / "crack_bridge.pt"
        if not model_path.is_file():
            self.skipTest("Production bridge checkpoint is not present")

        checkpoint_names = {
            0: "Crack",
            1: "Efflorescence_Leaching",
            2: "Exposed Rebar",
            3: "Spalling",
            4: "Staining_Infiltration",
            5: "Corrosion",
            6: "Biological_Growth",
            7: "Pothole Asphalt",
            8: "Expansion Joint",
            9: "Guardrail Damaged",
            10: "Control Point",
        }
        expected_permutation = {
            0: 6,
            1: 10,
            2: 5,
            3: 0,
            4: 1,
            5: 8,
            6: 2,
            7: 9,
            8: 7,
            9: 3,
            10: 4,
        }
        mapper = ModelClassRemapper(
            model_type="bridge",
            model_path=model_path,
            raw_names=checkpoint_names,
            config_path=project_root / "class_mapping_config.json",
        )

        self.assertTrue(mapper.mapping_applied)
        self.assertEqual(
            {
                raw_id: mapper.remap_detection(raw_id)["class_id"]
                for raw_id in checkpoint_names
            },
            expected_permutation,
        )


if __name__ == "__main__":
    unittest.main()
