"""Render Maya's exported skeletal animation to an MP4 using Blender.

blender --background --factory-startup --python render_animation_preview.py -- OUTPUT_DIR ASSET
The review adds endpoint holds and a reverse; it does not modify the Maya/FBX clip.
"""
import hashlib
import json
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector

folder, asset = Path(sys.argv[sys.argv.index('--') + 1]).resolve(), sys.argv[-1]
fbx = folder / (asset + '.fbx')
validation = json.loads((folder / 'validation_samples.json').read_text(encoding='utf-8'))
duration = float(validation['durationSeconds'])
steps = max(1, round(duration * 30))
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
scene = bpy.context.scene
scene.render.fps = 30
bpy.ops.import_scene.fbx(filepath=str(fbx), use_anim=True)
rigs = [o for o in scene.objects if o.type == 'ARMATURE' and o.animation_data and o.animation_data.action]
if not rigs:
    raise RuntimeError('FBX contains no animated armature')
start = min(o.animation_data.action.frame_range.x for o in rigs)
end = max(o.animation_data.action.frame_range.y for o in rigs)
meshes = [o for o in scene.objects if o.type == 'MESH']
points = []
for i in range(steps + 1):
    t = start + (end-start)*i/steps
    scene.frame_set(int(t), subframe=t-int(t))
    depsgraph = bpy.context.evaluated_depsgraph_get()
    for obj in meshes:
        evaluated = obj.evaluated_get(depsgraph)
        points.extend(evaluated.matrix_world @ Vector(p) for p in evaluated.bound_box)
minimum = Vector(tuple(min(p[i] for p in points) for i in range(3)))
maximum = Vector(tuple(max(p[i] for p in points) for i in range(3)))
center = (minimum + maximum) * .5
extent = (maximum - minimum).length
camdata = bpy.data.cameras.new('ReviewCamera')
camera = bpy.data.objects.new('ReviewCamera', camdata)
scene.collection.objects.link(camera)
scene.camera = camera
camera.location = center + Vector((1.5, -1, .9)).normalized() * extent * 2
camera.rotation_euler = (center - camera.location).to_track_quat('-Z', 'Y').to_euler()
camdata.type = 'ORTHO'
camdata.clip_start = .001
camdata.clip_end = extent * 10
bpy.context.view_layer.update()
projected = [camera.matrix_world.inverted() @ p for p in points]
camdata.ortho_scale = max(max(p.x for p in projected)-min(p.x for p in projected),
                        (max(p.y for p in projected)-min(p.y for p in projected))*4/3)*1.15
scene.render.engine = 'BLENDER_WORKBENCH'
scene.render.resolution_x, scene.render.resolution_y = 960, 720
scene.render.resolution_percentage = 100
scene.display.shading.light = 'STUDIO'
scene.display.shading.color_type = 'SINGLE'
scene.display.shading.single_color = (.58, .62, .66)
scene.display.shading.show_shadows = True
scene.display.shading.show_cavity = True
scene.display.shading.cavity_type = 'BOTH'
scene.display.shading.show_object_outline = True
scene.display.shading.background_type = 'WORLD'
scene.world.color = (.12, .15, .19)
scene.view_settings.view_transform = 'Standard'
frames = folder / 'preview_frames'
frames.mkdir(exist_ok=True)
scene.render.image_settings.file_format = 'PNG'
for i in range(steps + 1):
    t = start + (end-start)*i/steps
    scene.frame_set(math.floor(t), subframe=t-math.floor(t))
    scene.render.filepath = str(frames / ('pose_%03d.png' % i))
    bpy.ops.render.render(write_still=True)

# Encode the rendered frames in a separate scene, preserving the source action.
video = bpy.data.scenes.new('ReviewVideo')
bpy.context.window.scene = video
video.render.resolution_x, video.render.resolution_y = 960, 720
video.render.resolution_percentage = 100
video.render.fps = 30
video.view_settings.view_transform = 'Standard'
editor = video.sequence_editor_create()
order = [0]*15 + list(range(steps + 1)) + [steps]*30 + list(range(steps - 1, -1, -1)) + [0]*15
strips = editor.strips if hasattr(editor, 'strips') else editor.sequences
strip = strips.new_image('Animation Review', str(frames / ('pose_%03d.png' % order[0])), channel=1, frame_start=1)
for i in order[1:]:
    strip.elements.append('pose_%03d.png' % i)
video.frame_start, video.frame_end = 1, len(order)
video.render.image_settings.media_type = 'VIDEO'
video.render.image_settings.file_format = 'FFMPEG'
video.render.ffmpeg.format = 'MPEG4'
video.render.ffmpeg.codec = 'H264'
video.render.ffmpeg.constant_rate_factor = 'HIGH'
output = folder / (asset + '_animation_review.mp4')
video.render.filepath = str(output)
bpy.ops.render.render(animation=True)
def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
manifest = {'assetName': asset, 'preview': str(output), 'previewSha256': sha(output),
            'fbxSha256': sha(fbx), 'mayaFileSha256': sha(folder / (asset + '_Animated.mb')),
            'sourceFrameRange': [start, end], 'fps': 30, 'previewFrames': len(order),
            'previewDescription': 'Fixed camera; hold, %g-second unfold, hold, reverse, hold. Matte review shading.' % duration,
            'approvalStatus': 'pending', 'renderSource': 'Maya-authored rigid skeletal FBX rendered in Blender'}
(folder / 'animation_review.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
print('ANIMATION_PREVIEW_RESULT', str(output))
