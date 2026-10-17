"""Remove a redundant trailing slash without moving KLS backup object keys."""
from __future__ import annotations

import argparse
import copy
import json
import posixpath
import subprocess
from urllib.parse import urlsplit

NAMESPACE = 'keeping-law-simple'
RESOURCE = 'keeping-law-simple-postgres-ha-backup'
SERVER = 'keeping-law-simple-postgres-ha'


def destination_patch(resource: dict) -> list[dict]:
    original = resource['spec']['configuration']['destinationPath']
    parsed = urlsplit(original)
    if (parsed.scheme != 's3' or not parsed.netloc or parsed.query or parsed.fragment
            or parsed.username or parsed.password or any(ord(c) < 32 for c in original)):
        raise ValueError('Expected an S3 destination without credentials or query parameters')
    if not parsed.path.strip('/') or not original.endswith('/'):
        return []
    canonical = original.rstrip('/')
    new_path = urlsplit(canonical).path
    if '//' in new_path:
        raise ValueError('Interior path separators need a separate review')
    if posixpath.join(parsed.path, SERVER, 'base') != posixpath.join(new_path, SERVER, 'base'):
        raise ValueError('Normalization must not move existing backup keys')
    return [
        {'op': 'test', 'path': '/metadata/uid', 'value': resource['metadata']['uid']},
        {'op': 'test', 'path': '/metadata/resourceVersion', 'value': resource['metadata']['resourceVersion']},
        {'op': 'test', 'path': '/spec/configuration/destinationPath', 'value': original},
        {'op': 'replace', 'path': '/spec/configuration/destinationPath', 'value': canonical},
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Apply the guarded path-only correction')
    args = parser.parse_args()
    def k(*parts):
        return subprocess.check_output(['kubectl', *parts], text=True, timeout=60).strip()
    if (k('config', 'current-context') != 'kubernetes-admin@kubernetes'
            or k('config', 'view', '--minify', '-o', 'jsonpath={.clusters[0].cluster.server}') != 'https://192.168.104.50:6443'
            or json.loads(k('get', 'namespace', NAMESPACE, '-o', 'json'))['status']['phase'] != 'Active'):
        raise RuntimeError('Unexpected Kubernetes context, API server, or namespace')
    before = json.loads(k('-n', NAMESPACE, 'get', 'objectstores.barmancloud.cnpg.io', RESOURCE, '-o', 'json'))
    patch = destination_patch(before)
    if args.apply and patch:
        k('-n', NAMESPACE, 'patch', 'objectstores.barmancloud.cnpg.io', RESOURCE,
            '--type=json', '-p', json.dumps(patch))
        after = json.loads(k('-n', NAMESPACE, 'get', 'objectstores.barmancloud.cnpg.io', RESOURCE, '-o', 'json'))
        expected = copy.deepcopy(before['spec'])
        expected['configuration']['destinationPath'] = patch[-1]['value']
        if after['spec'] != expected or destination_patch(after):
            raise RuntimeError('The backup path correction did not read back exactly')
    print(json.dumps({'resource': RESOURCE, 'apply': args.apply, 'change_needed': bool(patch),
        'patch': patch, 'credentials_changed': False, 'retention_changed': False, 'delete_operations': 0}, indent=2))


if __name__ == '__main__':
    main()
