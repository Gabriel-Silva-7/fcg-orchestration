#!/usr/bin/env python3
"""Package clean Git sources locally, then restore verified detached clones. No publication."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
OWNERS = ('orchestration', 'users', 'catalog', 'payments', 'notifications')


def run(arguments, cwd=None):
    result = subprocess.run(arguments, cwd=cwd, capture_output=True, text=True, timeout=180)
    if result.returncode:
        raise ValueError('Git operation failed: ' + arguments[1])
    return result.stdout.strip()


def git(path, *arguments):
    return run(['git', *arguments], cwd=path)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def valid_origin(value):
    parsed = urlsplit(value)
    require(parsed.scheme == 'https' and parsed.hostname and not parsed.username
            and not parsed.password and not parsed.query and not parsed.fragment,
            'Expected HTTPS origin without credentials or query')
    return value


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def fresh_output(path):
    require(not path.exists() and not path.is_symlink(), 'Output already exists; inspect it instead of overwriting')
    path.mkdir(parents=True, mode=0o700)


def inspect_source(path):
    require(not git(path, 'status', '--porcelain', '--untracked-files=all'), 'Source checkout is dirty')
    require(git(path, 'rev-parse', '--show-toplevel') == str(path.resolve()), 'Source must be a repository root')
    return {'sha': git(path, 'rev-parse', 'HEAD'), 'origin': valid_origin(git(path, 'remote', 'get-url', 'origin'))}


def pack(sources, output):
    require(set(sources) == set(OWNERS), 'Exactly five sources are required')
    # Validate all inputs before creating output. Only committed Git objects are bundled.
    selected = {owner: inspect_source(Path(sources[owner]).resolve()) for owner in OWNERS}
    fresh_output(output)
    for owner in OWNERS:
        bundle = output / (owner + '.bundle')
        source = Path(sources[owner]).resolve()
        git(source, 'bundle', 'create', str(bundle.resolve()), 'HEAD')
        git(source, 'bundle', 'verify', str(bundle.resolve()))
        require(git(source, 'rev-parse', 'HEAD') == selected[owner]['sha'] and
                not git(source, 'status', '--porcelain', '--untracked-files=all'), 'Source changed while packaging')
        heads = git(source, 'bundle', 'list-heads', str(bundle.resolve())).splitlines()
        require(heads == [selected[owner]['sha'] + ' HEAD'], 'Bundle head does not match selected source')
        selected[owner].update({'bundle': bundle.name, 'sha256': digest(bundle)})
    manifest = {'schema': 1, 'kind': 'local-review-sources', 'repositories': selected}
    (output / 'sources.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest


def read_manifest(manifest_path):
    manifest = json.loads(manifest_path.read_text())
    require(manifest.get('schema') == 1 and manifest.get('kind') == 'local-review-sources', 'Unsupported source manifest')
    repositories = manifest.get('repositories', {})
    require(set(repositories) == set(OWNERS), 'Manifest must select exactly five repositories')
    for owner, entry in repositories.items():
        require(entry.get('bundle') == owner + '.bundle', 'Unexpected bundle path')
        require(re.fullmatch(r'[0-9a-f]{40}', entry.get('sha', '')) is not None, 'Expected full Git SHA')
        require(re.fullmatch(r'[0-9a-f]{64}', entry.get('sha256', '')) is not None, 'Expected SHA256')
        valid_origin(entry.get('origin', ''))
        bundle = manifest_path.parent / entry['bundle']
        require(bundle.is_file() and not bundle.is_symlink(), 'Missing bundle or symbolic link')
        require(digest(bundle) == entry['sha256'], 'Bundle hash mismatch: ' + owner)
        require(run(['git', 'bundle', 'list-heads', str(bundle.resolve())]).splitlines() ==
                [entry['sha'] + ' HEAD'], 'Bundle selected commit mismatch: ' + owner)
    return manifest


def restore(manifest_path, output):
    manifest_path = manifest_path.resolve()
    manifest = read_manifest(manifest_path)
    fresh_output(output)
    restored = {}
    for owner in OWNERS:
        entry = manifest['repositories'][owner]
        destination = output / ('fcg-orchestration' if owner == 'orchestration' else 'fcg-' + owner + '-api')
        run(['git', 'clone', '--no-checkout', str(manifest_path.parent / entry['bundle']), str(destination.resolve())])
        git(destination, 'checkout', '--detach', entry['sha'])
        git(destination, 'remote', 'set-url', 'origin', entry['origin'])
        require(git(destination, 'rev-parse', 'HEAD') == entry['sha'] and
                not git(destination, 'status', '--porcelain', '--untracked-files=all'), 'Restored source is not clean')
        restored[owner] = {'sha': entry['sha'], 'directory': destination.name}
    (output / 'RESTORED.json').write_text(json.dumps({'schema': 1, 'manifest_sha256': digest(manifest_path), 'repositories': restored}, indent=2) + '\n')
    return restored


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='operation', required=True)
    package = commands.add_parser('pack', help='Create five local bundles from clean selected sources')
    for owner in OWNERS[1:]:
        package.add_argument('--' + owner, type=Path, required=True)
    package.add_argument('--output', type=Path, required=True)
    checkout = commands.add_parser('restore', help='Verify bundles and clone selected commits to a new directory')
    checkout.add_argument('--manifest', type=Path, required=True)
    checkout.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.operation == 'pack':
        result = pack({'orchestration': ROOT, **{owner: getattr(args, owner) for owner in OWNERS[1:]}}, args.output.resolve())
        print(json.dumps({'state': 'packaged', 'sources': {owner: entry['sha'] for owner, entry in result['repositories'].items()}}))
    else:
        result = restore(args.manifest, args.output.resolve())
        print(json.dumps({'state': 'restored', 'sources': result}))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.TimeoutExpired) as error:
        # Paths/credentials from raw Git stderr are deliberately not printed.
        print('Source preparation failed: ' + (str(error) if isinstance(error, ValueError) else type(error).__name__), file=sys.stderr)
        sys.exit(1)
