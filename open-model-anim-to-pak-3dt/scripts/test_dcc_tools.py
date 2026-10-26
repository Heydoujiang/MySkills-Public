"""Small isolated Maya regression (never opens the user's production scenes).

Run: mayapy test_dcc_tools.py --output <new-test-directory>
Native viewport smoke testing is a separate native_playblast.py invocation.
"""
import argparse
import json
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError('Use a fresh isolated test directory')
    args.output.mkdir(parents=True)
    tools = Path(__file__).resolve().parents[1] / 'assets' / 'Tools' / 'Maya'
    sys.path.insert(0, str(tools))
    import maya.standalone
    maya.standalone.initialize(name='python')
    import maya.cmds as cmds
    import explode_pipeline as pipeline
    from dcc_workspace import sha256
    try:
        source = args.output / 'Fixture.ma'
        pipeline.create_fixture(source)
        source_hash = sha256(source)
        pipeline.open_geometry(source)
        inspection = pipeline.inspect_open_scene(source)
        config = pipeline.make_config(inspection, 'Fixture', 2.)
        # Include a rotating part, not just translated parts.
        moving = next(p for p in config['parts'] if any(p['offsetCm']))
        moving['rotationDegrees'] = [0., 8., 0.]
        config_file = args.output / 'config.json'
        pipeline.write_json(config_file, config)
        work = args.output / 'Work'
        pipeline.build(config_file, work)
        scene = work / 'Fixture_Animated.mb'
        pipeline.open_geometry(scene)
        cmds.setAttr('persp.translateX', 123.456)
        cmds.setAttr('fixture_metal.color', .1, .3, .6, type='double3')
        cmds.file(save=True, force=True)
        first_hash = sha256(scene)
        config['durationFrames'] = 66
        for part in config['parts']:
            part['offsetCm'] = [v * 1.2 for v in part['offsetCm']]
        pipeline.write_json(config_file, config)
        pipeline.update(config_file, scene, work)
        first_samples = json.loads((work / 'validation_samples.json').read_text())['samples']
        first_backup = json.loads((work / 'update_manifest.json').read_text())['backup']
        backup_manifest = json.loads((Path(first_backup) / 'backup_manifest.json').read_text())
        assert backup_manifest['files'][0]['sha256'] == first_hash
        old_validation_path = next(r['backup'] for r in backup_manifest['files'] if r['source'].endswith('validation_samples.json'))
        assert json.loads(Path(old_validation_path).read_text())['appliedConfig']['durationFrames'] == 60
        pipeline.update(config_file, scene, work)
        final_validation = json.loads((work / 'validation_samples.json').read_text())
        assert final_validation['samples'] == first_samples, 'Repeated update accumulated pose drift'
        assert abs(cmds.getAttr('persp.translateX') - 123.456) < 1e-8
        assert max(abs(a - b) for a, b in zip(cmds.getAttr('fixture_metal.color')[0], (.1, .3, .6))) < 1e-6
        assert sha256(source) == source_hash, 'Static source was modified'
        assert len(list((work / 'Backups').iterdir())) == 2
        # First build must never silently overwrite a revised working scene.
        unchanged = sha256(scene)
        try:
            pipeline.build(config_file, work)
        except FileExistsError:
            pass
        else:
            raise AssertionError('build overwrote an existing work scene')
        assert sha256(scene) == unchanged
        pipeline.export_working_rig(config_file, scene, args.output / 'Export')
        assert sha256(scene) == unchanged
        report = {'passed': True, 'sourceUntouched': True, 'sameWorkingScene': str(scene),
                  'repeatUpdateNoDrift': True, 'cameraAndMaterialsPreserved': True,
                  'twoVerifiedBackups': True, 'oldAppliedConfigPreserved': True,
                  'overwriteRejected': True, 'exportSceneUnchanged': True,
                  'maxRigidVertexErrorCm': final_validation['maxRigidVertexErrorCm']}
        (args.output / 'test_result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print('DCC_TEST_RESULT', json.dumps(report), flush=True)
    finally:
        maya.standalone.uninitialize()


if __name__ == '__main__':
    main()
