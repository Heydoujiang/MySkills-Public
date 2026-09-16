"""Blender background utility: fixed-camera assembled/exploded review images.

blender --background --factory-startup --python render_preview.py -- <output-dir> <asset-name>
"""
import sys
from pathlib import Path
import math

import bpy
from mathutils import Vector


args = sys.argv[sys.argv.index("--") + 1:]
folder, asset = Path(args[0]).resolve(), args[1]
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
scene = bpy.context.scene
scene.render.engine = "BLENDER_WORKBENCH"
scene.render.resolution_x = 1200
scene.render.resolution_y = 900
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = "PNG"
scene.world.color = (.82, .82, .82)
scene.display.shading.light = "STUDIO"
scene.display.shading.color_type = "MATERIAL"
scene.display.shading.show_shadows = True
scene.display.shading.show_cavity = True
scene.display.shading.cavity_type = "BOTH"
scene.display.shading.show_object_outline = True
scene.display.shading.background_type = "WORLD"
scene.display.shading.background_color = (.82, .82, .82)
scene.view_settings.view_transform = "Standard"
states = {}
all_points = []
for state in ("assembled", "exploded"):
    before = set(bpy.data.objects)
    bpy.ops.wm.obj_import(filepath=str(folder / (asset + "_" + state + ".obj")),
                          forward_axis="NEGATIVE_Z", up_axis="Y")
    objects = [o for o in bpy.data.objects if o not in before and o.type == "MESH"]
    states[state] = objects
    for obj in objects:
        all_points.extend(obj.matrix_world @ Vector(p) for p in obj.bound_box)
        obj.hide_render = True
minimum = Vector(tuple(min(p[i] for p in all_points) for i in range(3)))
maximum = Vector(tuple(max(p[i] for p in all_points) for i in range(3)))
center = (minimum + maximum) * .5
extent = (maximum - minimum).length
camera_data = bpy.data.cameras.new("review_camera")
camera = bpy.data.objects.new("review_camera", camera_data)
scene.collection.objects.link(camera)
scene.camera = camera
camera_data.type = "ORTHO"
camera_data.clip_end = extent * 10
views = {"oblique": Vector((1.5, -1., .9)),
         "side": Vector((1., 0., .04)),
         "end": Vector((.01, -1., .03))}
for view, direction in views.items():
    camera.location = center + direction.normalized() * extent * 2
    camera.rotation_euler = (center - camera.location).to_track_quat("-Z", "Y").to_euler()
    bpy.context.view_layer.update()
    inv = camera.matrix_basis.inverted()
    projected = [inv @ p for p in all_points]
    width = max(p.x for p in projected) - min(p.x for p in projected)
    height = max(p.y for p in projected) - min(p.y for p in projected)
    camera_data.ortho_scale = max(width, height * 1200 / 900) * 1.15
    for state, objects in states.items():
        for obj in objects:
            obj.hide_render = False
        scene.render.filepath = str(folder / (asset + "_preview_" + view + "_" + state + ".png"))
        bpy.ops.render.render(write_still=True)
        for obj in objects:
            obj.hide_render = True
print("PREVIEW_RESULT", str(folder))
