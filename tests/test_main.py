from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
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
            ("id: branch-001", "id: branch-"),
            ("10.21.2.0/24", "10.21.1.0/24"),
            ("10.21.1.0/24", "10.21.1.5/24"),
            ("profiles: [pos]", "profiles: [missing]"),
            ("10.200.10.0/24", "8.8.8.0/24"),
            ("version: 1", "version: 2"),
            ("version: 1", "version: true"),
            ("profiles: [pos]", "profiles: [pos, pos]"),
        )
        with tempfile.TemporaryDirectory() as directory:
            for old, new in cases:
                with self.subTest(replacement=new):
                    config = self.write_config(Path(directory), source.replace(old, new, 1))
                    with self.assertRaises(ConfigError):
                        load_inventory(config)

    def test_duplicate_yaml_keys_are_rejected(self) -> None:
        source = (ROOT / "config.example.yaml").read_text(encoding="utf-8")
        cases = (
            source + "\nversion: 1\n",
            source.replace("    lan: 10.21.1.0/24", "    lan: 10.21.1.0/24\n    lan: 10.21.9.0/24", 1),
            source.replace("  pos:\n", "  pos:\n    - 10.200.40.0/24\n  pos:\n", 1),
        )
        with tempfile.TemporaryDirectory() as directory:
            for number, text in enumerate(cases):
                with self.subTest(case=number):
                    config = self.write_config(Path(directory), text)
                    with self.assertRaisesRegex(ConfigError, "повторный ключ"):
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

    def test_diff_includes_changed_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            result = io.StringIO()
            with redirect_stdout(result):
                self.assertEqual(process(ROOT / "config.example.yaml", output, check=True, diff=True), 2)
            self.assertIn("--- current/hub/branch-lans.rsc", result.getvalue())
            self.assertIn("+++ expected/hub/branch-lans.rsc", result.getvalue())
            self.assertFalse(output.exists())

    def test_stale_files_trigger_check_without_deletion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            config = ROOT / "config.example.yaml"
            self.assertEqual(process(config, output, check=False), 0)
            orphan = output / "sites" / "branch-old.rsc"
            orphan.write_text("old\n", encoding="utf-8")
            self.assertEqual(process(config, output, check=True), 2)
            self.assertTrue(orphan.exists())

    def test_output_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target"
            target.mkdir()
            link = root / "output"
            try:
                link.symlink_to(target, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("Символьные ссылки недоступны")
            with self.assertRaisesRegex(ConfigError, "символьная ссылка"):
                process(ROOT / "config.example.yaml", link, check=False)
            self.assertEqual(list(target.iterdir()), [])

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
