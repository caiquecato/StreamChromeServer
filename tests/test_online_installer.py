from pathlib import Path
import importlib.util
import unittest

PROJECT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('online_builder', PROJECT / 'scripts/build_online_installer.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class OnlineInstallerTests(unittest.TestCase):
    def test_every_dependency_is_pinned_and_downloaded_from_its_official_host(self):
        entries = builder.load_dependencies(PROJECT / 'installer/dependencies.json')
        text = builder.dependency_entries(entries)
        self.assertEqual(len(entries), 5)
        self.assertEqual(text.count('Hash:'), 5)
        self.assertEqual(text.count('Flags: external download'), 5)
        self.assertEqual(text.count('extractarchive'), 3)

    def test_invalid_host_or_path_cannot_enter_compiler_input(self):
        entries = builder.load_dependencies(PROJECT / 'installer/dependencies.json')
        for field, value in [('url', 'http://example.invalid/payload'), ('destination', '..\\..'), ('filename', 'bad"; Flags: evil')]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                builder.dependency_entries([{**entries[0], field: value}])
