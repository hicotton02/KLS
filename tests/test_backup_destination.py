import copy
import posixpath
from urllib.parse import urlsplit

import pytest

from scripts.normalize_backup_destination import SERVER, destination_patch


def resource(destination='s3://backups/maintenance/kls/'):
    return {'metadata': {'uid': 'test-only', 'resourceVersion': '42'},
        'spec': {'configuration': {'destinationPath': destination}, 'retentionPolicy': '30d'}}


def test_path_fix_keeps_backup_keys_and_corrects_annotation_listing():
    original = resource()
    saved = copy.deepcopy(original)
    patch = destination_patch(original)
    assert original == saved
    assert [item['op'] for item in patch] == ['test', 'test', 'test', 'replace']
    old_path = urlsplit(original['spec']['configuration']['destinationPath']).path.lstrip('/')
    new_path = urlsplit(patch[-1]['value']).path.lstrip('/')
    old_catalog = posixpath.join(old_path, SERVER, 'base')
    new_catalog = posixpath.join(new_path, SERVER, 'base')
    assert old_catalog == new_catalog
    assert '//' in posixpath.join(old_path + '/', SERVER, 'base')
    assert posixpath.join(new_path + '/', SERVER, 'base') == new_catalog
    assert patch[-1]['path'] == '/spec/configuration/destinationPath'
    assert patch[-1]['value'] == 's3://backups/maintenance/kls'


@pytest.mark.parametrize('url', ['s3://backups/maintenance/kls', 's3://backups/', 's3://backups'])
def test_already_canonical_or_bucket_root_needs_no_change(url):
    assert destination_patch(resource(url)) == []


@pytest.mark.parametrize('url', ['https://example.com/kls/', 's3://user:password@backups/kls/',
    's3://backups/kls/?token=not-a-real-secret', 's3://backups/a//kls/', 's3://backups/kls/\n'])
def test_ambiguous_or_credential_bearing_paths_are_rejected(url):
    with pytest.raises(ValueError):
        destination_patch(resource(url))
