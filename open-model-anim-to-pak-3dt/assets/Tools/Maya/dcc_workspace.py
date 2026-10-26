"""Verified, non-destructive backups before revising an existing DCC work file."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import uuid


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def backup_working_scene(scene, companions=(), backup_root=None):
    """Copy the current saved file and optional reports, then verify every copy.

    Call before mutating the scene. Unsaved interactive edits must first be saved
    by their owner; this helper only backs up bytes already present on disk.
    """
    scene = Path(scene).resolve(strict=True)
    root = Path(backup_root).resolve() if backup_root else scene.parent / 'Backups'
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
    destination = root / (scene.stem + '_' + stamp + '_' + uuid.uuid4().hex[:6])
    destination.mkdir(parents=True, exist_ok=False)
    rows = []
    inputs = [scene] + [Path(p).resolve() for p in companions if Path(p).is_file()]
    for index, source in enumerate(dict.fromkeys(inputs)):
        target = destination / (source.name if index == 0 else '%02d_%s' % (index, source.name))
        before = sha256(source)
        shutil.copy2(source, target)
        if sha256(target) != before or sha256(source) != before:
            raise RuntimeError('Source changed while creating backup: ' + str(source))
        rows.append({'source': str(source), 'backup': str(target), 'sha256': before})
    manifest = {'createdUtc': stamp, 'workingScene': str(scene), 'files': rows,
                'restore': 'Copy the required backup to its recorded source path after closing the DCC.'}
    (destination / 'backup_manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    return destination
