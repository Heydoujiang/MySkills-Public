"""Single-view Maya viewport preview. Run in mayapy; never saves the input scene.

Use the existing review camera on revisions. --fit creates temporary framing only
for a first preview. Native compressed output can be delivered directly; AVI/none
is the tested Windows fallback and can optionally be compressed afterward.
"""
import argparse
import json
import math
from pathlib import Path
import struct
import time
from dcc_workspace import sha256


def avi_header(path):
    with Path(path).open('rb') as stream:
        header = stream.read(65536)
    if header[:4] != b'RIFF' or header[8:12] != b'AVI ' or b'avih' not in header:
        raise ValueError('Output is not a readable native AVI: ' + str(path))
    values = struct.unpack_from('<14I', header, header.index(b'avih') + 8)
    return {'frameCount': values[4], 'fps': 1e6 / values[0], 'resolution': [values[8], values[9]]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('scene', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--camera', default='persp')
    parser.add_argument('--width', type=int, default=960)
    parser.add_argument('--height', type=int, default=720)
    parser.add_argument('--start', type=int)
    parser.add_argument('--end', type=int)
    parser.add_argument('--format', default='avi')
    parser.add_argument('--compression', default='none')
    parser.add_argument('--fit', action='store_true')
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError('Keep or back up the existing preview before replacing it: ' + str(args.output))
    if args.width < 16 or args.height < 16:
        raise ValueError('Preview dimensions must be at least 16 pixels')
    import maya.standalone
    maya.standalone.initialize(name='python')
    import maya.cmds as cmds
    try:
        before = sha256(args.scene)
        cmds.file(str(args.scene.resolve()), open=True, force=True, executeScriptNodes=False, ignoreVersion=True)
        # playbackOptions are often restored by a saved script node. We disable
        # script nodes on load, so those UI defaults are not reliable clip bounds.
        manifest = args.scene.parent / 'validation_samples.json'
        motion_file = args.scene.parent / 'motion_config.json'
        metadata = json.loads(manifest.read_text(encoding='utf-8-sig')) if manifest.is_file() else {}
        motion = json.loads(motion_file.read_text(encoding='utf-8-sig')) if motion_file.is_file() else {}
        keys = cmds.keyframe(query=True, timeChange=True) or []
        if 'durationFrames' in metadata:
            range_start, range_end = 0, int(metadata['durationFrames'])
        elif 'endFrame' in motion:
            range_start, range_end = int(motion.get('startFrame', 1)), int(motion['endFrame'])
        elif keys:
            range_start, range_end = math.floor(min(keys)), math.ceil(max(keys))
        else:
            range_start, range_end = 0, 0
        start = args.start if args.start is not None else range_start
        end = args.end if args.end is not None else range_end
        if end <= start:
            raise ValueError('Preview end must be after start')
        if not cmds.objExists(args.camera):
            raise ValueError('Review camera does not exist: ' + args.camera)
        camera_shape = args.camera if cmds.nodeType(args.camera) == 'camera' else cmds.listRelatives(args.camera, shapes=True, type='camera')[0]
        camera_transform = cmds.listRelatives(camera_shape, parent=True, fullPath=True)[0]
        if args.fit:
            shapes = [s for s in cmds.ls(type='mesh', long=True) if not cmds.getAttr(s + '.intermediateObject')]
            if not shapes:
                raise ValueError('Scene has no preview meshes')
            bounds = []
            # First-view fit samples the entire motion; it is not a collision proof.
            for frame in range(start, end + 1):
                cmds.currentTime(frame, update=True)
                bounds.append(cmds.exactWorldBoundingBox(shapes))
            low = [min(b[i] for b in bounds) for i in range(3)]
            high = [max(b[i + 3] for b in bounds) for i in range(3)]
            center = [(a + b) / 2 for a, b in zip(low, high)]
            diagonal = math.dist(low, high)
            if diagonal <= 0:
                raise ValueError('Cannot fit a zero-size object')
            direction = [.65, .4, .7]
            position = [center[i] + direction[i] * diagonal * 2 for i in range(3)]
            cmds.xform(camera_transform, worldSpace=True, translation=position)
            target = cmds.spaceLocator(name='playblast_fit_target')[0]
            cmds.xform(target, worldSpace=True, translation=center)
            constraint = cmds.aimConstraint(target, camera_transform, aimVector=(0, 0, -1), upVector=(0, 1, 0), worldUpType='vector', worldUpVector=(0, 1, 0))[0]
            cmds.delete(constraint, target)
            cmds.setAttr(camera_shape + '.orthographic', True)
            cmds.setAttr(camera_shape + '.orthographicWidth', diagonal * 1.15 * max(1., args.width / args.height))
            cmds.setAttr(camera_shape + '.farClipPlane', max(10000., diagonal * 10))
        # standalone may have no active panel until playblast constructs its
        # viewport. The tested offscreen default uses persp in that case.
        try:
            cmds.lookThru(args.camera)
        except RuntimeError:
            if camera_transform.rsplit('|', 1)[-1] != 'persp':
                cmds.xform('persp', worldSpace=True, matrix=cmds.xform(camera_transform, query=True, worldSpace=True, matrix=True))
                for attr in ('orthographic', 'orthographicWidth', 'focalLength', 'horizontalFilmAperture', 'verticalFilmAperture', 'nearClipPlane', 'farClipPlane'):
                    cmds.setAttr('perspShape.' + attr, cmds.getAttr(camera_shape + '.' + attr))
        cmds.currentTime(start, update=True)
        cmds.select(clear=True)
        cmds.refresh(force=True)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        result = cmds.playblast(filename=str(args.output.resolve()), format=args.format, compression=args.compression,
                                quality=100, forceOverwrite=False, viewer=False, showOrnaments=False, offScreen=True,
                                percent=100, widthHeight=(args.width, args.height), startTime=start, endTime=end, clearCache=True)
        output = Path(result) if result and Path(result).is_file() else args.output
        if not output.is_file() or not output.stat().st_size:
            raise RuntimeError('Native playblast did not produce a video')
        if sha256(args.scene) != before:
            raise RuntimeError('Preview source scene changed unexpectedly')
        report = {'scene': str(args.scene.resolve()), 'sceneSha256': before, 'video': str(output.resolve()),
                  'videoSha256': sha256(output), 'fileBytes': output.stat().st_size, 'sourceSceneUnchanged': True,
                  'camera': args.camera, 'cameraMatrix': cmds.xform(camera_transform, query=True, worldSpace=True, matrix=True),
                  'singleView': True, 'previewMethod': 'Maya native offscreen playblast', 'format': args.format,
                  'compression': args.compression, 'startFrame': start, 'endFrame': end,
                  'playblastSeconds': round(time.monotonic() - started, 3)}
        if args.format == 'avi':
            report.update(avi_header(output))
            if report['frameCount'] != end - start + 1 or report['resolution'] != [args.width, args.height]:
                raise RuntimeError('Playblast frame count or resolution mismatch')
        output.with_name(output.stem + '_preview_manifest.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print('PLAYBLAST_RESULT', json.dumps(report), flush=True)
    finally:
        maya.standalone.uninitialize()


if __name__ == '__main__':
    main()
