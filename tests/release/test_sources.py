import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('release', Path(__file__).resolve().parents[2] / 'scripts/release.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class SourcePackageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="fcg release '$ ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.sources = {}
        for owner in release.OWNERS:
            path = self.root / owner
            path.mkdir()
            subprocess.run(['git', 'init', '-q', str(path)], check=True)
            release.git(path, 'config', 'user.email', 'fixture@example.invalid')
            release.git(path, 'config', 'user.name', 'Source fixture')
            release.git(path, 'remote', 'add', 'origin', 'https://example.invalid/' + owner + '.git')
            (path / 'README.md').write_text(owner)
            (path / '.gitignore').write_text('.env\n.tools/\n')
            release.git(path, 'add', '.')
            release.git(path, 'commit', '-qm', 'fixture')
            (path / '.env').write_text('PRIVATE_IGNORED_FIXTURE')
            (path / '.tools').mkdir()
            (path / '.tools/kubectl').write_text('IGNORED_DOWNLOADED_TOOL')
            self.sources[owner] = path
        self.package = self.root / 'package'

    def package_sources(self):
        return release.pack(self.sources, self.package)

    def test_roundtrip_clean_selected_heads_and_no_ignored_files(self):
        manifest = self.package_sources()
        output = self.root / 'new clones'
        result = release.restore(self.package / 'sources.json', output)
        for owner, entry in result.items():
            destination = output / entry['directory']
            self.assertEqual(entry['sha'], manifest['repositories'][owner]['sha'])
            self.assertFalse((destination / '.env').exists())
            self.assertFalse((destination / '.tools').exists())
            self.assertEqual('', release.git(destination, 'status', '--porcelain'))
            self.assertEqual('HEAD', release.git(destination, 'rev-parse', '--abbrev-ref', 'HEAD'))
            self.assertEqual(manifest['repositories'][owner]['origin'], release.git(destination, 'remote', 'get-url', 'origin'))
        self.assertNotIn(str(self.root), (self.package / 'sources.json').read_text())

    def test_dirty_source_refused_before_output(self):
        (self.sources['catalog'] / 'README.md').write_text('uncommitted')
        with self.assertRaisesRegex(ValueError, 'dirty'):
            self.package_sources()
        self.assertFalse(self.package.exists())

    def test_corrupt_bundle_refused_before_cloning(self):
        self.package_sources()
        with (self.package / 'users.bundle').open('ab') as stream:
            stream.write(b'corruption')
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            release.restore(self.package / 'sources.json', self.root / 'restore')
        self.assertFalse((self.root / 'restore').exists())

    def test_wrong_selected_commit_refused(self):
        manifest = self.package_sources()
        manifest['repositories']['catalog']['sha'] = 'a' * 40
        (self.package / 'sources.json').write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, 'commit mismatch'):
            release.restore(self.package / 'sources.json', self.root / 'restore')

    def test_existing_output_preserved(self):
        self.package_sources()
        output = self.root / 'existing'
        output.mkdir()
        (output / 'foreign').write_text('keep')
        with self.assertRaisesRegex(ValueError, 'already exists'):
            release.restore(self.package / 'sources.json', output)
        self.assertEqual('keep', (output / 'foreign').read_text())

    def test_manifest_escape_or_credential_origin_refused(self):
        manifest = self.package_sources()
        manifest['repositories']['users']['bundle'] = '../users.bundle'
        (self.package / 'sources.json').write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, 'bundle path'):
            release.restore(self.package / 'sources.json', self.root / 'restore')
        with self.assertRaisesRegex(ValueError, 'without credentials'):
            release.valid_origin('https://secret@example.invalid/repo')

    def test_symlink_bundle_refused(self):
        self.package_sources()
        bundle = self.package / 'users.bundle'
        copied = self.root / 'copy.bundle'
        bundle.rename(copied)
        bundle.symlink_to(copied)
        with self.assertRaisesRegex(ValueError, 'symbolic link'):
            release.restore(self.package / 'sources.json', self.root / 'restore')


if __name__ == '__main__':
    unittest.main()
