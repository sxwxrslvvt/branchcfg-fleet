from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from main import ConfigError, load_inventory, main, process, render_files

ROOT = Path(__file__).resolve().parents[1]


class InventoryTests(unittest.TestCase):
    def write_config(self, directory: Path, source: str) -> Path:
        path = directory / "test.yaml"
        path.write_text(source, encoding="utf-8")
        return path

    def test_deterministic_output_and_network_deduplication(self) -> None:
        inventory = load_inventory(ROOT / "config.example.yaml")
        first = render_files(inventory)
        self.assertEqual(first, render_files(inventory))
        self.assertEqual(len(first), 5)
        branch = first[Path("sites/branch-002.rsc")]
        self.assertEqual(branch.count("address=10.200.20.10/32"), 1)
        self.assertIn("branch-001", first[Path("hub/branch-lans.rsc")])

    def test_validation_rejects_unsafe_inventory(self) -> None:
        source = (ROOT / "config.example.yaml").read_text(encoding="utf-8")
        cases = (
            ("id: branch-001", "id: ../branch-001"),
            ("10.21.2.0/24", "10.21.1.0/24"),
            ("10.21.1.0/24", "10.21.1.5/24"),
            ("profiles: [pos]", "profiles: [missing]"),
            ("10.200.10.0/24", "8.8.8.0/24"),
            ("version: 1", "version: 2"),
            ("profiles: [pos]", "profiles: [pos, pos]"),
        )
        with tempfile.TemporaryDirectory() as directory:
            for old, new in cases:
                with self.subTest(replacement=new):
                    config = self.write_config(Path(directory), source.replace(old, new, 1))
                    with self.assertRaises(ConfigError):
                        load_inventory(config)

    def test_drift_is_detected_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            config = ROOT / "config.example.yaml"
            self.assertEqual(process(config, output, check=True), 2)
            self.assertFalse(output.exists())
            self.assertEqual(process(config, output, check=False), 0)
            self.assertEqual(process(config, output, check=True), 0)
            rendered = output / "sites" / "branch-001.rsc"
            rendered.write_text("changed\n", encoding="utf-8")
            self.assertEqual(process(config, output, check=True), 2)
            self.assertEqual(rendered.read_text(encoding="utf-8"), "changed\n")
            self.assertEqual(process(config, output, check=False), 0)
            self.assertEqual(process(config, output, check=True), 0)

    def test_stale_files_trigger_check_without_deletion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            config = ROOT / "config.example.yaml"
            self.assertEqual(process(config, output, check=False), 0)
            orphan = output / "sites" / "branch-old.rsc"
            orphan.write_text("old\n", encoding="utf-8")
            self.assertEqual(process(config, output, check=True), 2)
            self.assertTrue(orphan.exists())

    def test_invalid_yaml_and_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bad = self.write_config(Path(directory), "sites: [invalid\n")
            with self.assertRaises(ConfigError):
                load_inventory(bad)
            self.assertEqual(main(["check", "--config", str(bad)]), 1)
            with self.assertRaises(ConfigError):
                load_inventory(Path(directory) / "missing.yaml")


if __name__ == "__main__":
    unittest.main()
