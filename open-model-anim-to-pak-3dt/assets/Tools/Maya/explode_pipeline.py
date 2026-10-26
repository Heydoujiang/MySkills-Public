"""Reusable rigid-part exploded-view authoring, run with Maya's mayapy.

Commands: inspect, create-config, build, update, export, fixture. Static sources are never saved.
Build creates the work scene once. Update backs it up and edits that same scene.
The motion configuration describes presentation layout, not service procedures.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
from dcc_workspace import backup_working_scene, sha256

import maya.standalone
import maya.cmds as cmds
import maya.mel as mel
import maya.api.OpenMaya as om


SCHEMA_VERSION = 1


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def safe_name(value):
    value = re.sub(r"[^A-Za-z0-9_]", "_", value)
    return value if value and not value[0].isdigit() else "part_" + value


def require_plugin(name):
    if not cmds.pluginInfo(name, query=True, loaded=True):
        cmds.loadPlugin(name, quiet=True)


def open_geometry(source):
    suffix = Path(source).suffix.lower()
    if suffix not in (".mb", ".ma", ".fbx", ".obj"):
        raise ValueError("Supported geometry inputs: .mb, .ma, .fbx, .obj")
    cmds.file(new=True, force=True)
    if suffix == ".fbx":
        require_plugin("fbxmaya")
    elif suffix == ".obj":
        require_plugin("objExport")
    cmds.file(str(Path(source).resolve()), open=True, force=True,
              executeScriptNodes=False, ignoreVersion=True)
    # All manifests and animation offsets use Maya centimeters.
    cmds.currentUnit(linear="cm")


def mesh_function(shape):
    sel = om.MSelectionList()
    sel.add(shape)
    return om.MFnMesh(sel.getDagPath(0))


def visible_mesh_shapes():
    return sorted(shape for shape in cmds.ls(type="mesh", long=True) or []
                  if not cmds.getAttr(shape + ".intermediateObject"))


def inspect_open_scene(source):
    shapes = visible_mesh_shapes()
    if not shapes:
        raise ValueError("The source has no non-intermediate mesh shapes")
    grouped = {}
    for shape in shapes:
        parent = cmds.listRelatives(shape, parent=True, fullPath=True)[0]
        grouped.setdefault(parent, []).append(shape)
    parts = []
    for source_path, part_shapes in sorted(grouped.items()):
        bounds = cmds.exactWorldBoundingBox(part_shapes)
        pid = "p_" + hashlib.sha1(source_path.encode("utf-8")).hexdigest()[:12]
        leaf = source_path.rsplit("|", 1)[-1]
        materials = set()
        for shape in part_shapes:
            for sg in cmds.listConnections(shape, type="shadingEngine") or []:
                materials.update(cmds.listConnections(sg + ".surfaceShader", source=True,
                                                       destination=False) or [])
        parts.append({"partId": pid, "sourcePath": source_path,
                      "sourceName": leaf, "sourceShapes": part_shapes,
                      "meshName": "mesh_" + safe_name(leaf) + "_" + pid[2:],
                      "boneName": "b_" + safe_name(leaf) + "_" + pid[2:],
                      "boundsCm": bounds,
                      "centerCm": [(bounds[i] + bounds[i + 3]) / 2 for i in range(3)],
                      "sizeCm": [bounds[i + 3] - bounds[i] for i in range(3)],
                      "vertices": sum(cmds.polyEvaluate(s, vertex=True) for s in part_shapes),
                      "triangles": sum(cmds.polyEvaluate(s, triangle=True) for s in part_shapes),
                      "materials": sorted(materials)})
    bounds = cmds.exactWorldBoundingBox(shapes)
    return {"schemaVersion": SCHEMA_VERSION, "source": str(Path(source).resolve()),
            "sourceSha256": sha256(source),
            "linearUnit": "cm", "sourceUpAxis": cmds.upAxis(query=True, axis=True),
            "partCount": len(parts), "meshShapeCount": len(shapes),
            "vertices": sum(p["vertices"] for p in parts),
            "triangles": sum(p["triangles"] for p in parts),
            "boundsCm": bounds,
            "assemblyCenterCm": [(bounds[i] + bounds[i + 3]) / 2 for i in range(3)],
            "assemblySizeCm": [bounds[i + 3] - bounds[i] for i in range(3)],
            "parts": parts,
            "notes": ["partId is a SHA-1 prefix of the original full transform path; "
                      "renaming/reparenting a source part requires regenerating or remapping its config.",
                      "Non-intermediate shapes sharing a transform form one rigid part."]}


def make_config(info, name, duration_seconds=5.5):
    sizes = info["assemblySizeCm"]
    primary, secondary, thin = sorted(range(3), key=lambda i: sizes[i], reverse=True)
    # Use the largest bounding-volume part as a stationary presentation anchor.
    anchor = max(info["parts"], key=lambda p: math.prod(max(v, .01) for v in p["sizeCm"]))
    core = anchor["centerCm"]
    longest = max(sizes)
    parts = []
    for part in info["parts"]:
        offset = [0., 0., 0.]
        group = "anchor"
        start, end = 0., 1.
        if part["partId"] != anchor["partId"]:
            rel = [(part["centerCm"][i] - core[i]) / max(sizes[i], .01) for i in range(3)]
            # Three axis-aligned banks keep the layout legible; this does not infer assembly order.
            if abs(rel[primary]) > .28:
                axis = primary
                lane = "end"
                distance = longest * .15
                start, end = .22, .94
            elif abs(rel[secondary]) > .16:
                axis = secondary
                lane = "upper_lower"
                distance = longest * .17
                start, end = .12, .83
            else:
                axis = thin
                lane = "side"
                distance = longest * .14
                start, end = .06, .72
            sign = 1. if rel[axis] >= 0 else -1.
            # Small, repeatable depth bands separate overlapping layers without random scattering.
            rank = min(2, int(abs(rel[axis]) * 6))
            distance *= 1. + rank * .15
            offset[axis] = sign * distance
            group = "visual_%s_%s_%d" % (lane, "positive" if sign > 0 else "negative", rank)
        parts.append({"partId": part["partId"], "sourcePath": part["sourcePath"],
                      "boneName": part["boneName"], "motionGroup": group,
                      "offsetCm": offset, "rotationDegrees": [0., 0., 0.],
                      "start": start, "end": end})
    return {"schemaVersion": SCHEMA_VERSION, "assetName": safe_name(name),
            "source": info["source"], "sourceSha256": info["sourceSha256"],
            "fps": 30, "durationFrames": round(duration_seconds * 30), "linearUnit": "cm",
            "recenter": True, "uniformScale": 1.0,
            "easing": "beta_3_4", "fbxVersion": "FBX201800",
            "layoutMethod": "overlapping_axis_banks_v2",
            "notes": ["First-pass visual layout; motion groups are not real-world disassembly steps.",
                      "First-pass timing is a suggestion, not a fixed clip duration or physical assembly order.",
                      "Edit offsets, optional pathOffsetsCm, rotations and overlapping start/end progress; use update after first build.",
                      "0 is assembled; 1 is exploded. This asset contains no return or camera animation.",
                      "Offsets are centimeters in the original Maya scene axes after recentering/scaling."],
            "parts": parts}


def validate_config(config, info):
    if config.get("schemaVersion") != SCHEMA_VERSION:
        raise ValueError("Unsupported motion config schemaVersion")
    if config.get("fps") != 30 or config.get("durationFrames", 0) <= 0:
        raise ValueError("This pipeline requires fps=30 and a positive durationFrames")
    if config.get("linearUnit") != "cm" or config.get("easing") not in ("smoothstep", "smootherstep", "beta_3_4"):
        raise ValueError("Expected centimeters and smoothstep, smootherstep or beta_3_4 easing")
    if not (math.isfinite(config.get("uniformScale", 0)) and config["uniformScale"] > 0):
        raise ValueError("uniformScale must be positive and finite")
    source_parts = {p["partId"]: p for p in info["parts"]}
    configured = [p["partId"] for p in config["parts"]]
    if len(configured) != len(set(configured)) or set(configured) != set(source_parts):
        raise ValueError("The configuration must contain each source part exactly once")
    if config.get("sourceSha256") != info["sourceSha256"]:
        raise ValueError("Source file changed: inspect and regenerate/remap the configuration explicitly")
    for part in config["parts"]:
        if part["sourcePath"] != source_parts[part["partId"]]["sourcePath"]:
            raise ValueError("partId/sourcePath mapping mismatch")
        if part["boneName"] != source_parts[part["partId"]]["boneName"]:
            raise ValueError("boneName must retain the stable source-part mapping")
        if not 0 <= part["start"] < part["end"] <= 1:
            raise ValueError("Each part requires 0 <= start < end <= 1")
        for key in ("offsetCm", "rotationDegrees"):
            if len(part[key]) != 3 or not all(math.isfinite(v) for v in part[key]):
                raise ValueError(key + " must contain three finite values")
        if "pathOffsetsCm" in part:
            points = part["pathOffsetsCm"]
            if len(points) < 2 or any(len(p) != 3 or not all(math.isfinite(v) for v in p) for p in points):
                raise ValueError("pathOffsetsCm needs at least two finite xyz points")
            if points[0] != [0, 0, 0] or points[-1] != part["offsetCm"]:
                raise ValueError("pathOffsetsCm must start at zero and finish at offsetCm")


def ease(progress, start, end, model="beta_3_4"):
    t = max(0., min(1., (progress - start) / (end - start)))
    if model == "smoothstep":
        return t * t * (3. - 2. * t)
    if model == "smootherstep":
        return t ** 3 * (10. - 15. * t + 6. * t * t)
    # The tested beta(3,4) CDF: zero speed/acceleration at both endpoints,
    # with a longer settling tail. Cohorts overlap instead of adding a hard pause.
    return 20. * t ** 3 - 45. * t ** 4 + 36. * t ** 5 - 10. * t ** 6


def path_offset(motion, amount):
    points = motion.get("pathOffsetsCm", [[0., 0., 0.], motion["offsetCm"]])
    lengths = [math.dist(a, b) for a, b in zip(points, points[1:])]
    total = sum(lengths)
    if total <= 1e-12:
        return list(points[-1])
    target = min(1., max(0., amount)) * total
    traversed = 0.
    for a, b, length in zip(points, points[1:], lengths):
        if length and target <= traversed + length:
            t = (target - traversed) / length
            return [a[i] + (b[i] - a[i]) * t for i in range(3)]
        traversed += length
    return list(points[-1])


def key_records(records, config, root="root"):
    for rec in records:
        cmds.cutKey(rec["bone"], attribute=("tx", "ty", "tz", "rx", "ry", "rz"), clear=True)
    for frame in range(config["durationFrames"] + 1):
        for rec in records:
            motion = rec["motion"]
            amount = ease(frame / config["durationFrames"], motion["start"], motion["end"], config["easing"])
            offset = path_offset(motion, amount)
            values = [rec["pivot"][i] + offset[i] for i in range(3)] + [v * amount for v in motion["rotationDegrees"]]
            for attr, value in zip(("tx", "ty", "tz", "rx", "ry", "rz"), values):
                cmds.setKeyframe(rec["bone"], attribute=attr, time=frame, value=value,
                                inTangentType="linear", outTangentType="linear")
    cmds.cutKey(root, attribute=("tx", "ty", "tz", "rx", "ry", "rz"), clear=True)
    for frame in (0, config["durationFrames"]):
        for attr in ("tx", "ty", "tz", "rx", "ry", "rz"):
            cmds.setKeyframe(root, attribute=attr, time=frame, value=0,
                            inTangentType="linear", outTangentType="linear")


def matrix_of(node):
    return om.MMatrix(cmds.xform(node, query=True, worldSpace=True, matrix=True))


def matrix_data(node):
    mat = matrix_of(node)
    tm = om.MTransformationMatrix(mat)
    q = tm.rotation(asQuaternion=True)
    t = tm.translation(om.MSpace.kWorld)
    return {"translationCm": list(t), "rotationXyzw": [q.x, q.y, q.z, q.w],
            "scale": list(tm.scale(om.MSpace.kWorld)), "matrixRowMajor": list(mat)}


def vertex_samples(shape):
    points = mesh_function(shape).getPoints(om.MSpace.kWorld)
    count = len(points)
    indexes = sorted(set(round(i * (count - 1) / 11) for i in range(12)))
    return [(i, om.MPoint(points[i])) for i in indexes]


def export_fbx(path, meshes, root, frames, version):
    require_plugin("fbxmaya")
    mel.eval("FBXResetExport;")
    settings = ["FBXExportFileVersion -v %s" % version,
                "FBXExportSmoothingGroups -v true", "FBXExportTangents -v true",
                "FBXExportSmoothMesh -v false", "FBXExportSkins -v true",
                "FBXExportShapes -v false", "FBXExportConstraints -v false",
                "FBXExportCameras -v false", "FBXExportLights -v false",
                "FBXExportInputConnections -v false", "FBXExportEmbeddedTextures -v false",
                "FBXExportBakeComplexAnimation -v true", "FBXExportBakeComplexStart -v 0",
                "FBXExportBakeComplexEnd -v %d" % frames,
                "FBXExportBakeComplexStep -v 1", "FBXExportBakeResampleAnimation -v true",
                "FBXExportApplyConstantKeyReducer -v false"]
    for setting in settings:
        mel.eval(setting + ";")
    cmds.select([root] + meshes, replace=True)
    mel.eval('FBXExport -f "%s" -s;' % str(path).replace("\\", "/"))


def export_obj(path, meshes):
    require_plugin("objExport")
    cmds.select(meshes, replace=True)
    cmds.file(str(path), force=True, options="groups=1;ptgroups=0;materials=1;smoothing=1;normals=1",
              type="OBJexport", exportSelected=True)


def build(config_path, output):
    config = json.loads(Path(config_path).read_text(encoding="utf-8-sig"))
    source = config["source"]
    open_geometry(source)
    info = inspect_open_scene(source)
    validate_config(config, info)
    out = Path(output).resolve()
    name = config["assetName"]
    if safe_name(name) != name:
        raise ValueError("assetName must be a simple alphanumeric/underscore name")
    destinations = [out / (name + suffix) for suffix in
                    ("_Animated.mb", ".fbx", "_assembled.obj", "_exploded.obj")]
    if any(str(p.resolve()).casefold() == str(Path(source).resolve()).casefold()
           for p in destinations):
        raise ValueError("Output would overwrite the source model; choose another directory/name")
    if destinations[0].exists():
        raise FileExistsError("Work scene already exists; use update --scene: " + str(destinations[0]))
    out.mkdir(parents=True, exist_ok=True)
    write_json(out / "inspection.json", info)
    write_json(out / "motion_config.json", config)
    initial_roots = cmds.ls(assemblies=True, long=True)
    center = info["assemblyCenterCm"] if config["recenter"] else [0., 0., 0.]
    scale = config["uniformScale"]
    config_parts = {p["partId"]: p for p in config["parts"]}
    records = []
    max_source_error = 0.
    for part in info["parts"]:
        source_points = [mesh_function(s).getPoints(om.MSpace.kWorld)
                         for s in part["sourceShapes"]]
        duplicate = cmds.duplicate(part["sourcePath"], renameChildren=True,
                                   returnRootsOnly=True, name=part["meshName"])[0]
        # All source transforms are evaluated before flattening; no inherited motion is exported.
        if cmds.listRelatives(duplicate, parent=True):
            duplicate = cmds.parent(duplicate, world=True)[0]
        duplicate = cmds.rename(duplicate, part["meshName"])
        descendants = cmds.listRelatives(duplicate, allDescendents=True, fullPath=True) or []
        children_to_delete = [x for x in descendants if cmds.nodeType(x) == "transform"]
        if children_to_delete:
            cmds.delete(children_to_delete)
        cmds.delete(duplicate, constructionHistory=True)
        shapes = cmds.listRelatives(duplicate, shapes=True, fullPath=True, noIntermediate=True) or []
        cmds.makeIdentity(duplicate, apply=True, translate=True, rotate=True, scale=True,
                          normal=0, preserveNormals=True)
        # Baking world-space points avoids pivot, negative-scale, and parent-transform ambiguity.
        shape_points = [(shape, mesh_function(shape).getPoints(om.MSpace.kWorld)) for shape in shapes]
        cmds.xform(duplicate, worldSpace=True, matrix=list(om.MMatrix()))
        for shape, points in shape_points:
            fn = mesh_function(shape)
            transformed = om.MPointArray([om.MPoint((p.x - center[0]) * scale,
                                                  (p.y - center[1]) * scale,
                                                  (p.z - center[2]) * scale) for p in points])
            fn.setPoints(transformed, om.MSpace.kWorld)
        if len(shapes) != len(source_points):
            raise RuntimeError("Source shape count changed while preparing " + part["sourcePath"])
        for shape, original_points in zip(shapes, source_points):
            rest_points = mesh_function(shape).getPoints(om.MSpace.kWorld)
            if len(rest_points) != len(original_points):
                raise RuntimeError("Source vertex count changed while preparing " + part["sourcePath"])
            for original, actual in zip(original_points, rest_points):
                expected = om.MPoint(*[(original[i] - center[i]) * scale for i in range(3)])
                max_source_error = max(max_source_error, (actual - expected).length())
        pivot = [(part["centerCm"][i] - center[i]) * scale for i in range(3)]
        cmds.xform(duplicate, worldSpace=True, pivots=pivot)
        samples = []
        for shape in shapes:
            samples.append((shape, vertex_samples(shape)))
        records.append({"part": part, "motion": config_parts[part["partId"]],
                        "mesh": duplicate, "pivot": pivot, "samples": samples})
    for node in initial_roots:
        if cmds.objExists(node) and not cmds.listRelatives(node, shapes=True, type="camera"):
            cmds.delete(node)
    cmds.currentUnit(time="ntsc")
    cmds.playbackOptions(minTime=0, maxTime=config["durationFrames"],
                         animationStartTime=0, animationEndTime=config["durationFrames"])
    cmds.currentTime(0)
    cmds.select(clear=True)
    root = cmds.joint(name="root", position=(0, 0, 0))
    cmds.setAttr(root + ".radius", max(info["assemblySizeCm"]) * .003)
    for rec in records:
        cmds.select(root, replace=True)
        bone = cmds.joint(name=rec["part"]["boneName"], position=rec["pivot"])
        rec["bone"] = bone
        # Exactly one influence per rigid part; no blending or simulation.
        skin = cmds.skinCluster(bone, rec["mesh"], toSelectedBones=True,
                                maximumInfluences=1, obeyMaxInfluences=True,
                                normalizeWeights=1, name="skin_" + rec["part"]["partId"])[0]
        cmds.skinPercent(skin, rec["mesh"], transformValue=[(bone, 1.0)])
        rec["skin"] = skin
        rec["restMatrix"] = matrix_of(bone)
    key_records(records, config, root)
    checks = []
    sample_progress = sorted(set([i / 10 for i in range(11)] + [.037, .283, .619, .947]))
    max_rigid_error = 0.
    for progress in sample_progress:
        cmds.currentTime(progress * config["durationFrames"], update=True)
        bones = {"root": matrix_data(root)}
        sample_error = 0.
        for rec in records:
            delta = rec["restMatrix"].inverse() * matrix_of(rec["bone"])
            bones[rec["bone"]] = matrix_data(rec["bone"])
            for shape, samples in rec["samples"]:
                actual = mesh_function(shape).getPoints(om.MSpace.kWorld)
                for index, original in samples:
                    expected = original * delta
                    error = (actual[index] - expected).length()
                    sample_error = max(sample_error, error)
        max_rigid_error = max(max_rigid_error, sample_error)
        checks.append({"progress": progress, "frame": progress * config["durationFrames"],
                       "maxRigidVertexErrorCm": sample_error, "bones": bones})
    if max_rigid_error > .002:
        raise RuntimeError("Rigid binding validation failed: %.9f cm" % max_rigid_error)
    if max_source_error > .002:
        raise RuntimeError("Original source geometry validation failed: %.9f cm" % max_source_error)
    name = config["assetName"]
    meshes = [r["mesh"] for r in records]
    # Export assembled/rest pose and a complete one-direction timeline.
    cmds.currentTime(0, update=True)
    export_obj(out / (name + "_assembled.obj"), meshes)
    export_fbx(out / (name + ".fbx"), meshes, root,
               config["durationFrames"], config["fbxVersion"])
    cmds.currentTime(config["durationFrames"], update=True)
    export_obj(out / (name + "_exploded.obj"), meshes)
    cmds.currentTime(0, update=True)
    cmds.file(rename=str(out / (name + "_Animated.mb")))
    cmds.file(save=True, type="mayaBinary", force=True)
    validation = {"schemaVersion": SCHEMA_VERSION, "assetName": name,
                  "authoringRig": "open_model_rigid_v2", "appliedConfig": config,
                  "fps": config["fps"], "durationFrames": config["durationFrames"],
                  "durationSeconds": config["durationFrames"] / config["fps"],
                  "coordinateConvention": {
                      "space": "Maya model space: root is identity, no actor transform",
                      "handedness": "right", "upAxis": info["sourceUpAxis"], "linearUnit": "cm",
                      "matrix": "row-major 4x4, row-vector convention, translation at indices 12..14",
                      "quaternion": "xyzw", "rotationOrder": "xyz",
                      "ueNote": "FBX importer performs basis conversion. These values are Maya-space; "
                      "do not compare directly against Unreal-space coordinates without basis conversion."},
                  "sourceToModel": {"subtractCenterCm": center, "uniformScale": scale},
                  "partCount": len(records), "boneCount": len(records) + 1,
                  "samplesPerShape": 12, "maxRigidVertexErrorCm": max_rigid_error,
                  "assembledBindPoseMaxErrorCm": max_source_error,
                  "sourceGeometryMaxErrorCm": max_source_error,
                  "sourceGeometryCheck": "All source vertices versus flattened rest vertices after explicit center/scale transform",
                  "toleranceCm": .002, "passed": True,
                  "parts": [{"partId": r["part"]["partId"], "boneName": r["bone"],
                             "meshName": r["mesh"], "motionGroup": r["motion"]["motionGroup"],
                             "materialSlots": r["part"]["materials"],
                             "restTranslationCm": r["pivot"]} for r in records],
                  "samples": checks}
    write_json(out / "validation_samples.json", validation)
    print("BUILD_RESULT", json.dumps({"output": str(out), "parts": len(records),
                                       "bones": len(records) + 1, "validationPassed": True,
                                       "maxRigidVertexErrorCm": max_rigid_error}))


def update(config_path, scene, output):
    """Revise a pipeline-created rig without replacing meshes, materials or camera."""
    scene = Path(scene).resolve(strict=True)
    out = Path(output).resolve()
    out.mkdir(parents=True, exist_ok=True)
    config = json.loads(Path(config_path).read_text(encoding="utf-8-sig"))
    base = scene.parent
    inspection = json.loads((base / "inspection.json").read_text(encoding="utf-8-sig"))
    previous = json.loads((base / "validation_samples.json").read_text(encoding="utf-8-sig"))
    if any("restTranslationCm" not in p for p in previous.get("parts", [])):
        raise ValueError("This is not a pipeline-created rigid rig. Adapt its transform-animation tracks explicitly; do not rebuild from the static source")
    if "appliedConfig" not in previous:
        raise ValueError("Legacy rig lacks its applied config snapshot. Verify and add the previous matching config to validation_samples.json before updating")
    if sha256(inspection["source"]) != inspection["sourceSha256"]:
        raise ValueError("Static source changed; explicitly remap parts before updating")
    if scene == Path(inspection["source"]).resolve():
        raise ValueError("The static source cannot be the working animation scene")
    validate_config(config, inspection)
    if previous["assetName"] != config["assetName"]:
        raise ValueError("Asset identity changed; do not overwrite an unrelated working scene")
    if previous["sourceToModel"] != {"subtractCenterCm": inspection["assemblyCenterCm"] if config["recenter"] else [0., 0., 0.], "uniformScale": config["uniformScale"]}:
        raise ValueError("An update cannot change source-to-model scale/recentering; explicitly revise geometry separately")
    backup = backup_working_scene(scene, [base / "motion_config.json", base / "inspection.json", base / "validation_samples.json"])
    open_geometry(scene)
    cmds.currentUnit(time="ntsc")
    cmds.currentTime(0, update=True)
    records = []
    by_id = {p["partId"]: p for p in previous["parts"]}
    for motion in config["parts"]:
        old = by_id[motion["partId"]]
        bone, mesh = old["boneName"], old["meshName"]
        if not cmds.objExists(bone) or not cmds.objExists(mesh):
            raise ValueError("Working scene no longer matches recorded rig: " + bone)
        current = matrix_data(bone)
        if any(abs(current["translationCm"][i] - old["restTranslationCm"][i]) > 1e-5 for i in range(3)) or any(abs(v) > 1e-7 for v in current["rotationXyzw"][:3]) or any(abs(v - 1.) > 1e-7 for v in current["scale"]):
            raise ValueError("Recorded assembled bone pose changed; resolve it explicitly before updating: " + bone)
        shapes = cmds.listRelatives(mesh, shapes=True, fullPath=True, noIntermediate=True) or []
        records.append({"bone": bone, "mesh": mesh, "pivot": old["restTranslationCm"], "motion": motion,
                        "restMatrix": matrix_of(bone), "samples": [(shape, vertex_samples(shape)) for shape in shapes]})
    key_records(records, config)
    cmds.playbackOptions(minTime=0, maxTime=config["durationFrames"], animationStartTime=0, animationEndTime=config["durationFrames"])
    samples, max_error = [], 0.
    for progress in sorted(set([i / 10 for i in range(11)] + [.037, .283, .619, .947])):
        frame = progress * config["durationFrames"]
        cmds.currentTime(frame, update=True)
        bones = {"root": matrix_data("root")}
        error = 0.
        for rec in records:
            bones[rec["bone"]] = matrix_data(rec["bone"])
            delta = rec["restMatrix"].inverse() * matrix_of(rec["bone"])
            for shape, points in rec["samples"]:
                fn = mesh_function(shape)
                for index, rest in points:
                    error = max(error, (fn.getPoint(index, om.MSpace.kWorld) - rest * delta).length())
        max_error = max(max_error, error)
        samples.append({"progress": progress, "frame": frame, "bones": bones, "maxRigidVertexErrorCm": error})
    if max_error > .002:
        raise RuntimeError("Rigid update check failed; work file has not been saved")
    previous.update(durationFrames=config["durationFrames"], durationSeconds=config["durationFrames"] / 30,
                    samples=samples, maxRigidVertexErrorCm=max_error, backup=str(backup), appliedConfig=config)
    for part in previous["parts"]:
        part["motionGroup"] = next(m["motionGroup"] for m in config["parts"] if m["partId"] == part["partId"])
    cmds.currentTime(0, update=True)
    # Saving the same path keeps camera, material and scene references stable.
    cmds.file(save=True, force=True, type="mayaAscii" if scene.suffix.lower() == ".ma" else "mayaBinary")
    write_json(base / "motion_config.json", config)
    write_json(base / "validation_samples.json", previous)
    if out != base:
        write_json(out / "validation_samples.json", previous)
    result = {"workingScene": str(scene), "sceneSha256": sha256(scene), "backup": str(backup),
              "maxRigidVertexErrorCm": max_error, "animationApprovalRequired": True}
    write_json(out / "update_manifest.json", result)
    print("UPDATE_RESULT", json.dumps(result))


def export_working_rig(config_path, scene, output):
    """Export the saved/approved working rig without rebuilding or saving it."""
    config = json.loads(Path(config_path).read_text(encoding="utf-8-sig"))
    scene = Path(scene).resolve(strict=True)
    validation = json.loads((scene.parent / "validation_samples.json").read_text(encoding="utf-8-sig"))
    if validation.get("appliedConfig") != config:
        raise ValueError("Config differs from the saved rig snapshot; update and obtain preview approval first")
    before = sha256(scene)
    out = Path(output).resolve()
    out.mkdir(parents=True, exist_ok=True)
    destination = out / (config["assetName"] + ".fbx")
    if destination.exists():
        raise FileExistsError("Back up the previous export before replacement: " + str(destination))
    open_geometry(scene)
    cmds.currentTime(0, update=True)
    meshes = [p["meshName"] for p in validation["parts"]]
    export_fbx(destination, meshes, "root", config["durationFrames"], config["fbxVersion"])
    if sha256(scene) != before:
        raise RuntimeError("Working scene unexpectedly changed during export")
    write_json(out / "validation_samples.json", validation)
    result = {"fbx": str(destination), "fbxSha256": sha256(destination), "sceneSha256": before,
              "sourceSceneUnchanged": True, "durationSeconds": config["durationFrames"] / 30}
    write_json(out / "export_manifest.json", result)
    print("EXPORT_RESULT", json.dumps(result))


def create_fixture(path):
    if Path(path).exists():
        raise ValueError("Fixture source already exists; reuse it or choose a new output path")
    cmds.file(new=True, force=True)
    cmds.currentUnit(linear="cm")
    cmds.upAxis(axis="y", rotateView=False)
    material = cmds.shadingNode("lambert", asShader=True, name="fixture_metal")
    cmds.setAttr(material + ".color", .20, .35, .48, type="double3")
    sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name="fixture_metalSG")
    cmds.connectAttr(material + ".outColor", sg + ".surfaceShader", force=True)
    meshes = []
    for name, position, size in [("housing", (0, 0, 0), (12, 6, 10)),
                                 ("lid", (0, 3.8, 0), (12, 1.2, 10)),
                                 ("base", (0, -3.8, 0), (14, 1.2, 12)),
                                 ("side_panel", (6.5, 0, 0), (1, 5, 8))]:
        node = cmds.polyCube(name=name, width=size[0], height=size[1], depth=size[2])[0]
        cmds.xform(node, translation=position)
        meshes.append(node)
    for i, (x, z) in enumerate([(-4, -3), (4, -3), (-4, 3), (4, 3)]):
        node = cmds.polyCylinder(name="fastener_%02d" % (i + 1), radius=.4, height=2,
                                 subdivisionsX=12)[0]
        cmds.xform(node, translation=(x, 4.4, z))
        meshes.append(node)
    for i, x in enumerate((-3., 0., 3.)):
        node = cmds.polyTorus(name="ring_%02d" % (i + 1), radius=1.1, sectionRadius=.25,
                              subdivisionsX=16, subdivisionsY=8)[0]
        cmds.xform(node, translation=(x, 0, 0), rotation=(0, 0, 90))
        meshes.append(node)
    cmds.sets(meshes, edit=True, forceElement=sg)
    cmds.group(meshes, name="fixture")
    cmds.delete(meshes, constructionHistory=True)
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    cmds.file(rename=str(path))
    cmds.file(save=True, force=True, type="mayaAscii")
    print("FIXTURE_RESULT", str(path))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    inspect_cmd = sub.add_parser("inspect")
    inspect_cmd.add_argument("source")
    inspect_cmd.add_argument("--output", required=True)
    config_cmd = sub.add_parser("create-config")
    config_cmd.add_argument("source")
    config_cmd.add_argument("--output", required=True)
    config_cmd.add_argument("--name", required=True)
    config_cmd.add_argument("--duration-seconds", type=float, default=5.5)
    build_cmd = sub.add_parser("build")
    build_cmd.add_argument("config")
    build_cmd.add_argument("--output", required=True)
    update_cmd = sub.add_parser("update")
    update_cmd.add_argument("config")
    update_cmd.add_argument("--scene", required=True)
    update_cmd.add_argument("--output", required=True)
    export_cmd = sub.add_parser("export")
    export_cmd.add_argument("config")
    export_cmd.add_argument("--scene", required=True)
    export_cmd.add_argument("--output", required=True)
    fixture_cmd = sub.add_parser("fixture")
    fixture_cmd.add_argument("--output", required=True)
    args = parser.parse_args()
    maya.standalone.initialize(name="python")
    try:
        if args.command == "fixture":
            create_fixture(args.output)
        elif args.command == "build":
            build(args.config, args.output)
        elif args.command == "update":
            update(args.config, args.scene, args.output)
        elif args.command == "export":
            export_working_rig(args.config, args.scene, args.output)
        else:
            open_geometry(args.source)
            info = inspect_open_scene(args.source)
            if args.command == "create-config" and (not math.isfinite(args.duration_seconds) or args.duration_seconds <= 0):
                raise ValueError("duration-seconds must be positive and finite")
            write_json(args.output, info if args.command == "inspect" else make_config(info, args.name, args.duration_seconds))
            print("INSPECTION_RESULT", json.dumps({k: info[k] for k in
                  ("partCount", "meshShapeCount", "vertices", "triangles", "boundsCm")}))
    finally:
        maya.standalone.uninitialize()


if __name__ == "__main__":
    main()
