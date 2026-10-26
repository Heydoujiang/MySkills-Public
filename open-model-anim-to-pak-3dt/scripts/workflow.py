"""Portable, background-only helpers. No credentials, project paths or approvals embedded."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
DCC_PRIORITY = ("maya", "3dsmax", "blender", "houdini")
PATH_FIELDS = ("project", "engine", "customEngine", "targetProject", "reports", "fbx", "dccScene", "preview",
               "approval", "partitionManifest", "samples", "collisionReport", "blueprintFile", "thumbnail",
               "profileFile", "validationReport", "pieReport", "migrationReport", "collectionManifest", "dccExecutable")


def load_config(path):
    """Resolve portable relative paths against the JSON that owns them."""
    path = Path(path).resolve(); c = read(path); c["_configDir"] = str(path.parent)
    for field in PATH_FIELDS:
        if c.get(field) and not Path(c[field]).is_absolute():
            c[field] = str((path.parent / c[field]).resolve())
    return c


def object_configs(c):
    if "objects" not in c:
        validate_identity(c)
        return [c]
    if not isinstance(c["objects"], list) or not c["objects"]:
        raise ValueError("objects must contain at least one per-object config path")
    result = []
    for entry in c["objects"]:
        if not isinstance(entry, str):
            raise ValueError("objects entries must be per-object JSON config paths")
        child = load_config(Path(c.get("_configDir", ".")) / entry)
        if "objects" in child:
            raise ValueError("Nested collection configs are not supported")
        for key in ("project", "engine", "customEngine", "targetProject", "format"):
            if key in c:
                if key != "format" and key in child and Path(child[key]).resolve() != Path(c[key]).resolve():
                    raise ValueError(f"Conflicting collection {key}: {entry}")
                child[key] = c[key]
        validate_identity(child); result.append(child)
    ids = [o["objectId"].casefold() for o in result]
    if len(ids) != len(set(ids)):
        raise ValueError("Each objectId must be unique (case insensitive)")
    reports = [str(Path(o["reports"]).resolve()).casefold() for o in result]
    if len(reports) != len(set(reports)):
        raise ValueError("Each object needs a separate reports directory")
    return result


def validate_identity(c):
    for field, value in (("objectId", c["objectId"]), ("hotkey", c.get("hotkey", "N"))):
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", value):
            raise ValueError(f"{field} must be an ASCII Unreal identifier")
    root = c.get("assetRoot", "/Game/" + c["objectId"])
    if not re.fullmatch(r"/Game/[A-Za-z][A-Za-z0-9_/]*", root):
        raise ValueError("assetRoot must be a safe /Game package root")


def asset_root(c):
    return c.get("assetRoot", "/Game/" + c["objectId"])


def pie_context(c):
    root = asset_root(c)
    return {"objectId": c["objectId"], "hotkey": c.get("hotkey", "N"),
            "map": c.get("pieMap") or c.get("map") or root + "/NoUMG/Demo_ExplodedAssembly_NoUMG",
            "blueprintAsset": c.get("blueprintAsset") or root + "/NoUMG/BP_ExplodedAssembly_NoUMG.BP_ExplodedAssembly_NoUMG",
            "blueprintSha256": sha(c["blueprintFile"])}


def check_pie_index(path):
    path = Path(path)
    if not path.is_file(): raise RuntimeError("This PIE run did not produce its own index.json")
    report = read(path)
    matches = [test for test in report.get("tests", []) if test.get("fullTestPath") == "OpenModel.NoUMG.PIE"]
    if (report.get("failed") != 0 or report.get("notRun") != 0 or
            report.get("succeeded", 0) + report.get("succeededWithWarnings", 0) < 1 or len(matches) != 1):
        raise RuntimeError("This PIE run did not complete OpenModel.NoUMG.PIE successfully")
    selected = matches[0]
    if (selected.get("state") != "Success" or selected.get("errors") != 0 or
            any(item.get("event", {}).get("type", "").lower() == "error" for item in selected.get("entries", []))):
        raise RuntimeError("OpenModel.NoUMG.PIE did not finish in Success state without errors")
    return report

def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))

def write(path, data):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

def sha(path):
    with open(path, "rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest().upper()

def run(argv, log, cwd=None, timeout=None):
    log = Path(log); log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("w", encoding="utf-8") as out:
        p = subprocess.run([str(x) for x in argv], cwd=cwd, stdout=out,
                           stderr=subprocess.STDOUT, timeout=timeout,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if p.returncode:
        raise RuntimeError(f"Exit {p.returncode}; see {log}")

def approval(config):
    if "objects" in config:
        for child in object_configs(config): approval(child)
        return
    a = read(config["approval"])
    if a.get("status") != "approved" or not a.get("userConfirmation", "").strip():
        raise ValueError("DCC preview approval is pending; obtain actual user confirmation")
    scene_key = "dccSceneSha256" if "dccSceneSha256" in a else "mayaFileSha256"
    for key, field in (("fbxSha256", "fbx"), (scene_key, "dccScene"), ("previewSha256", "preview")):
        if a.get(key, "").upper() != sha(config[field]):
            raise ValueError(f"Approved revision changed: {field}")
    if "mayaFileSha256" in a and a["mayaFileSha256"].upper() != sha(config["dccScene"]):
        raise ValueError("Conflicting legacy Maya scene hash")
    if config.get("partitionManifest"):
        path = Path(config["partitionManifest"]); manifest = read(path)
        if a.get("partitionManifestSha256", "").upper() != sha(path):
            raise ValueError("Partition manifest changed; reconfirm geometry-preserving export")
        if not all(manifest.get(k) is True for k in ("passed", "allGeometryExactlyOnce", "completeSkeletonAndAnimationRetainedInEveryGroup")):
            raise ValueError("Partition geometry/skeleton/animation audit failed")
        source = Path(manifest["source"])
        if not source.is_absolute(): source = path.parent / source
        if a.get("partitionSourceSha256", "").upper() != sha(source) or manifest.get("sourceSha256", "").upper() != sha(source):
            raise ValueError("Partition source changed")
        groups = manifest.get("groups", [])
        if not groups: raise ValueError("Partition groups cannot be empty")
        seen = set(); geometry = set()
        for index, group in enumerate(groups):
            name = group["assetName"]
            if not re.fullmatch(r"SK_[A-Za-z][A-Za-z0-9_]*", name) or name.casefold() in seen:
                raise ValueError("Invalid/duplicate partition asset name")
            if index == 0 and name != "SK_" + config["objectId"]:
                raise ValueError("First partition asset must be SK_<objectId>")
            seen.add(name.casefold())
            file = Path(group["fbx"])
            if not file.is_absolute(): file = path.parent / file
            if group.get("sha256", "").upper() != sha(file): raise ValueError("Partition FBX changed")
            ids = group.get("geometryIds", [])
            if not ids or len(ids) != len(set(ids)) or geometry.intersection(ids):
                raise ValueError("Partition geometry is empty or duplicated")
            geometry.update(ids)

def discover():
    names = {"maya": ("mayapy.exe", "mayapy"), "3dsmax": ("3dsmaxbatch.exe",),
             "blender": ("blender.exe", "blender"), "houdini": ("hython.exe", "hython")}
    found = {k: set(filter(None, (shutil.which(n) for n in v))) for k, v in names.items()}
    roots = [Path(os.environ.get("ProgramFiles", "C:/Program Files")), Path("C:/Software"), Path("D:/Software")]
    patterns = {"maya": ("Autodesk/Maya*/bin/mayapy.exe", "Maya*/bin/mayapy.exe", "maya/Maya*/bin/mayapy.exe"),
                "blender": ("Blender Foundation/Blender*/blender.exe", "Blender*/blender.exe"),
                "houdini": ("Side Effects Software/Houdini*/bin/hython.exe",),
                "3dsmax": ("Autodesk/3ds Max*/3dsmaxbatch.exe",)}
    for root in roots:
        if root.exists():
            for kind, globs in patterns.items():
                for glob in globs:
                    found[kind].update(str(p) for p in root.glob(glob) if p.is_file())
    # Read installed application locations as additional discovery, never scan entire drives.
    if os.name == "nt":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Autodesk\Maya") as parent:
                for i in range(winreg.QueryInfoKey(parent)[0]):
                    version = winreg.EnumKey(parent, i)
                    try:
                        with winreg.OpenKey(parent, version + r"\Setup\InstallPath") as key:
                            exe = Path(winreg.QueryValueEx(key, "MAYA_INSTALL_LOCATION")[0]) / "bin/mayapy.exe"
                            if exe.is_file(): found["maya"].add(str(exe))
                    except OSError: pass
        except OSError: pass
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for keypath in (r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"):
                try:
                    with winreg.OpenKey(hive, keypath) as parent:
                        for i in range(winreg.QueryInfoKey(parent)[0]):
                            try:
                                with winreg.OpenKey(parent, winreg.EnumKey(parent, i)) as child:
                                    loc = Path(winreg.QueryValueEx(child, "InstallLocation")[0])
                                    if str(loc) == ".": continue
                                    for kind, exes in names.items():
                                        for exe in exes:
                                            for sub in ("", "bin"):
                                                p = loc / sub / exe
                                                if p.is_file(): found[kind].add(str(p))
                            except OSError: pass
                except OSError: pass
    return {"candidates": {k: sorted(found[k], reverse=True) for k in DCC_PRIORITY},
            "priority": list(DCC_PRIORITY), "default": next((k for k in DCC_PRIORITY if found[k]), None),
            "otherDccSearchComplete": False,
            "note": "Built-in candidates only; Houdini does not exhaust other DCCs. Run verify-dcc, then inspect local installation records for other DCCs if these fail. Confirm FBX and native viewport support separately."}

def verify_dcc(c):
    """Run a tiny background scene operation; discovery alone is not usability proof."""
    found = discover()["candidates"]; explicit = c.get("dcc")
    if explicit and explicit not in DCC_PRIORITY:
        raise ValueError("No bundled startup probe for this DCC; verify its local scripting/license support before use")
    if c.get("dccExecutable") and not explicit:
        raise ValueError("dcc is required with a user-selected dccExecutable")
    candidates = [(kind, exe) for kind in ((explicit,) if explicit else DCC_PRIORITY)
                  for exe in ([c["dccExecutable"]] if c.get("dccExecutable") else found[kind])]
    reports = Path(c["reports"]); reports.mkdir(parents=True, exist_ok=True); attempts = []
    for kind, exe in candidates:
        if not Path(exe).is_file():
            attempts.append({"dcc": kind, "executable": exe, "error": "Executable missing"}); continue
        with tempfile.TemporaryDirectory(prefix="dcc-probe-", dir=reports) as temp:
            temp = Path(temp); marker = temp / "started.txt"; token = uuid.uuid4().hex
            emit = f"from pathlib import Path; Path({str(marker)!r}).write_text({token!r})"
            if kind == "maya":
                code = "import maya.standalone\nmaya.standalone.initialize(name='python')\nimport maya.cmds as cmds\ncmds.createNode('transform', name='OpenModelProbe')\n" + emit + "\nmaya.standalone.uninitialize()\n"
            elif kind == "blender":
                code = "import bpy\nbpy.data.objects.new('OpenModelProbe', None)\n" + emit + "\n"
            elif kind == "houdini":
                code = "import hou\nhou.node('/obj').createNode('geo', 'OpenModelProbe')\n" + emit + "\n"
            else:
                # This script creates only an unsaved, disposable scene.
                filename = str(marker).replace("\\", "/").replace('"', '\\"')
                code = f'resetMaxFile #noPrompt\np = box name:"OpenModelProbe"\nif isValidNode p do (f = createFile "{filename}"; format "{token}" to:f; close f)\n'
            script = temp / ("probe.ms" if kind == "3dsmax" else "probe.py")
            script.write_text(code, encoding="utf-8")
            argv = [exe, script]
            if kind == "blender": argv = [exe, "--background", "--factory-startup", "--python", script]
            log = reports / f"dcc-probe-{kind}-{len(attempts)+1}.log"
            try:
                run(argv, log, timeout=c.get("dccProbeTimeout", 120))
                if not marker.is_file() or marker.read_text(encoding="utf-8").strip() != token:
                    raise RuntimeError("Startup did not execute the scene probe (check license/startup log)")
                result = {"dcc": kind, "executable": str(Path(exe).resolve()), "startupVerified": True,
                          "explicitSelection": bool(explicit), "attempts": attempts,
                          "note": "Background scene creation passed; FBX export and native preview still need an actual adapter test."}
                write(reports / "dcc-probe.json", result); print(json.dumps(result, ensure_ascii=False, indent=2)); return result
            except (RuntimeError, OSError, subprocess.TimeoutExpired) as error:
                attempts.append({"dcc": kind, "executable": exe, "error": str(error), "log": str(log)})
    next_action = ("Resolve startup/license availability for the user's selected DCC; do not switch without authorization."
                   if explicit else "Inspect local installation records for other DCCs and verify any additional candidates before asking the user for a DCC.")
    write(reports / "dcc-probe.json", {"startupVerified": False, "attempts": attempts,
          "explicitSelection": bool(explicit), "otherDccSearchComplete": False, "nextAction": next_action})
    raise RuntimeError("Selected DCC could not start" if explicit else
                       "Built-in DCC candidates unavailable; other DCCs have not been exhausted. " + next_action)


def materialize(c):
    object_configs(c)  # Check IDs, but never rewrite a compiled template per model.
    project = Path(c["project"])
    destination = project.parent / "Plugins/OpenModelBuilder"
    if destination.exists(): raise FileExistsError(f"Reconcile or back up existing plugin first: {destination}")
    shutil.copytree(ROOT / "assets/OpenModelBuilder", destination)
    # Editor-only builder: no native runtime parent required by the generated actor.
    data = read(project); plugins = data.setdefault("Plugins", [])
    existing = next((p for p in plugins if p["Name"] == "OpenModelBuilder"), None)
    if existing: existing["Enabled"] = True
    else: plugins.append({"Name": "OpenModelBuilder", "Enabled": True})
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    shutil.copy2(project, project.with_suffix(f".uproject.before-explode-{stamp}")); write(project, data)
    print(destination)

def _partition_import(c, args, out, root):
    """Sequential imports; only exact source+tool+output receipts allow restart reuse."""
    manifest_path = Path(c["partitionManifest"]); manifest = read(manifest_path)
    content = Path(c["project"]).parent / "Content" / root.removeprefix("/Game/") / "Model"
    plugin = Path(c["project"]).parent / "Plugins/OpenModelBuilder"
    tool_hashes = {str(p.relative_to(plugin)): sha(p) for p in sorted(plugin.rglob("*"))
                   if p.suffix in (".cpp", ".h", ".inl", ".cs", ".uplugin")}
    skeleton = content / ("SK_" + c["objectId"] + "_Skeleton.uasset")
    for index, group in enumerate(manifest["groups"]):
        label = f"import-group-{index+1:02d}"; log = out / (label + ".log")
        receipt_path = out / (label + ".receipt.json")
        source = Path(group["fbx"])
        if not source.is_absolute(): source = manifest_path.parent / source
        outputs = [content / (group["assetName"] + ".uasset")]
        if index == 0:
            outputs += [skeleton, content / ("SK_" + c["objectId"] + "_Anim.uasset")]
        fingerprint = {"manifestSha256": sha(manifest_path), "sourceSha256": sha(source), "assetName": group["assetName"],
                       "engineExecutableSha256": sha(args[0]), "pluginSourceSha256": tool_hashes,
                       "sharedSkeletonSha256": sha(skeleton) if index else None,
                       "root": root, "project": str(Path(c["project"]).resolve())}
        if any(p.exists() for p in outputs):
            receipt = read(receipt_path) if receipt_path.exists() else {}
            actual = {str(p): sha(p) for p in outputs if p.is_file()}
            if receipt.get("fingerprint") == fingerprint and len(actual) == len(outputs) and actual == receipt.get("outputs"):
                print(f"REUSE_VERIFIED_PARTITION {index+1}", flush=True); continue
            raise FileExistsError(f"Partition {index+1} has existing or changed assets without a matching import receipt; back up and rebuild in a clean project")
        group_args = [v for v in args if not str(v).startswith(("-Fbx=", "-Name=", "-abslog="))]
        group_args += ["-Name=" + group["assetName"][3:], "-Fbx=" + str(source), "-abslog=" + str(log)]
        if "-ImportOnly" not in group_args: group_args.append("-ImportOnly")
        if index:
            group_args += ["-MeshOnly", f"-SharedSkeleton={root}/Model/SK_{c['objectId']}_Skeleton.SK_{c['objectId']}_Skeleton"]
        run(group_args, out / (label + "-stdout.log"))
        marker = "OPENMODEL_BUILD_OK" if index == 0 else "OPENMODEL_PARTITION_IMPORTED"
        if not log.exists() or marker not in log.read_text(encoding="utf-8", errors="replace") or not all(p.is_file() for p in outputs):
            raise RuntimeError(f"Partition {index+1} import did not prove successful; inspect {log}")
        write(receipt_path, {"fingerprint": fingerprint, "outputs": {str(p): sha(p) for p in outputs}})


def ue(c, stage):
    if "objects" in c:
        objects = object_configs(c)
        for obj in objects: approval(obj)
        if stage == "build-plugin":
            ue(objects[0], stage)
        elif stage == "prepare" and c.get("format") == "3dt":
            project = Path(c["project"]); target = Path(c["targetProject"]); out = Path(c["reports"])
            migration_report = Path(c.get("migrationReport", out / "migration.json"))
            migration_report.parent.mkdir(parents=True, exist_ok=True)
            if project.resolve() == target.resolve(): raise ValueError("Authoring and target preset must be separate")
            # Collection package dependencies are the already classified library actors.
            if not c.get("collectionManifest"):
                for obj in objects:
                    name = "OpenModel_" + obj["objectId"]
                    bp = project.parent / f"Plugins/JC_CustomAssets/Content/ObjectLibrary/Exhibition/OpenModel/{name}/{name}.uasset"
                    if not bp.is_file(): raise ValueError("Classify each object with .pak ue-prepare before creating a collection .3dt")
            args = [Path(c["engine"]) / "Engine/Binaries/Win64/UE4Editor-Cmd.exe", project,
                    "-run=OpenModelNoUMG", "-PrepareCollectionPackage", "-ObjectIds=" + "+".join(o["objectId"] for o in objects),
                    "-Map=" + c["map"], "-Destination=" + str(target.parent / "Content"), "-TargetProject=" + str(target),
                    "-CustomRoot=" + c["customEngine"], "-Report=" + str(migration_report),
                    "-unattended", "-nop4", "-nosound", "-nullrhi", "-abslog=" + str(out / "prepare.log")]
            if c.get("collectionManifest"): args.append("-CollectionManifest=" + c["collectionManifest"])
            run(args, out / "prepare-stdout.log")
            migration = read(migration_report)
            if migration.get("businessActorCount") != len(objects) or {a["objectId"] for a in migration.get("actors", [])} != {o["objectId"] for o in objects}:
                raise ValueError("Collection actor membership differs from requested objects")
        else:
            for obj in objects: ue(obj, stage)
        return
    approval(c)
    project = Path(c["project"]); out = Path(c["reports"]); name = c["objectId"]
    validate_identity(c); root = asset_root(c)
    if stage == "build-plugin":
        run([Path(c["engine"]) / "Engine/Binaries/DotNET/UnrealBuildTool.exe", "UE4Editor", "Win64", "Development",
             f"-Project={project}", "-WaitMutex", "-NoHotReloadFromIDE", "-2019", "-gather"], out / "build-plugin.log")
        return
    args = [Path(c["engine"]) / "Engine/Binaries/Win64/UE4Editor-Cmd.exe", project, "-unattended", "-nop4", "-nosound", "-nullrhi",
            f"-ObjectId={name}", f"-Hotkey={c.get('hotkey', 'N')}", f"-Root={root}",
            f"-ShaderWorkingDir={out / 'ShaderWork'}", f"-abslog={out / (stage + '.log')}"]
    if stage == "import":
        args += ["-run=OpenModelBuild", f"-Name={name}", f'-Fbx={c["fbx"]}']
        if c.get("importOnly"): args += ["-ImportOnly"]
        if c.get("partitionManifest"):
            _partition_import(c, args, out, root)
            args = [v for v in args if v != "-ImportOnly"]
            args += ["-SkipImport", "-PartitionManifest=" + c["partitionManifest"]]
    elif stage == "controls":
        args += ["-run=OpenModelNoUMG"]
        if c.get("rebuildControls"): args += ["-Rebuild"]
    elif stage == "free-camera":
        args += ["-run=OpenModelNoUMG", "-FreeCamera", f'-OutputMap={c["map"]}']
    elif stage == "collision":
        args += ["-run=OpenModelNoUMG", "-EnsureCollision", f"-Blueprint={root}/NoUMG/BP_ExplodedAssembly_NoUMG.BP_ExplodedAssembly_NoUMG",
                 f'-Report={c["collisionReport"]}', f'-CollisionProfile={c.get("collisionProfile", "BlockAllDynamic")}',
                 f'-SampleRate={c.get("collisionSampleRate", 120)}', f'-MarginCm={c.get("collisionMarginCm", 1)}']
    elif stage in ("validate", "validate-relocated"):
        assetroot = f"{root}/NoUMG" if stage == "validate" else f"/JC_CustomAssets/ObjectLibrary/Exhibition/OpenModel/OpenModel_{name}"
        bp = "BP_ExplodedAssembly_NoUMG" if stage == "validate" else f"OpenModel_{name}"
        validation_report = Path(c.get("validationReport", out / (stage + ".json")))
        validation_report.parent.mkdir(parents=True, exist_ok=True)
        args = [v for v in args if not str(v).startswith("-Root=")]
        args += ["-run=OpenModelValidate", "-NoUMG", f"-Root={assetroot}", f"-BPName={bp}",
                 f'-Samples={c["samples"]}', f'-Report={validation_report}']
    elif stage == "pie":
        pie_run = out / "PIE/runs" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ_") + uuid.uuid4().hex)
        pie_run.mkdir(parents=True, exist_ok=False)
        pie_passed = Path(c.get("pieReport", out / "PIE/passed.json"))
        if pie_passed.exists(): shutil.copy2(pie_passed, pie_run / "previous-passed.json")
        pie_attempt = dict(pie_context(c), passed=False, status="running", fullTestPath="OpenModel.NoUMG.PIE",
                           startedUtc=datetime.now(timezone.utc).isoformat(), runDirectory=str(pie_run.resolve()))
        write(pie_run / "run.json", pie_attempt)
        write(pie_passed, pie_attempt)  # A failed/new attempt cannot reuse an earlier success.
        args[0] = Path(c["engine"]) / "Engine/Binaries/Win64/UE4Editor.exe"
        args = [v for v in args if v not in ("-nullrhi",) and not str(v).startswith("-abslog=")]
        args += ["-RenderOffscreen", "-windowed", "-ResX=960", "-ResY=720", "-nosplash",
                 "-ExecCmds=Automation RunTests OpenModel.NoUMG.PIE", "-TestExit=Automation Test Queue Empty",
                 "-ReportExportPath=" + str(pie_run), "-NoUMGReport=" + str(pie_run),
                 "-abslog=" + str(pie_run / "pie.log"), "-Blueprint=" + pie_attempt["blueprintAsset"], "-Map=" + pie_attempt["map"]]
    elif stage == "prepare":
        fmt = c.get("format")
        if fmt not in ("pak", "3dt"): raise ValueError("Ask user for output format")
        target = Path(c["targetProject"])
        if target.resolve() == project.resolve(): raise ValueError("Prepare must use an isolated work project and a distinct preset target")
        migration_report = Path(c.get("migrationReport", out / "migration.json"))
        migration_report.parent.mkdir(parents=True, exist_ok=True)
        args += ["-run=OpenModelNoUMG", f'-CustomRoot={c["customEngine"]}', f"-TargetProject={target}", f'-Report={migration_report}']
        if fmt == "3dt": args += ["-PreparePackage", f'-Destination={target.parent / "Content"}', f'-Map={c["map"]}']
        else:
            args += ["-PreparePakBranch", f'-Destination={target.parent / "Plugins/JC_CustomAssets/Content"}', f'-Thumbnail={c["thumbnail"]}']
    else: raise ValueError(stage)
    if stage == "pie":
        try:
            run(args, pie_run / "stdout.log")
            index = pie_run / "index.json"; check_pie_index(index)
            if pie_context(c) != {key: pie_attempt[key] for key in pie_context(c)}:
                raise RuntimeError("Blueprint or interaction configuration changed during PIE")
            pie_attempt.update(passed=True, status="completed", indexSha256=sha(index),
                               scope="Actual offscreen PIE keyboard and Details interactions")
        except Exception as error:
            pie_attempt.update(passed=False, status="failed", error=str(error)); raise
        finally:
            pie_attempt["finishedUtc"] = datetime.now(timezone.utc).isoformat()
            write(pie_run / "run.json", pie_attempt); write(pie_passed, pie_attempt)
    else:
        run(args, out / (stage + "-stdout.log"))

def configure_profile(c):
    fmt = c.get("format")
    if fmt not in ("pak", "3dt"): raise ValueError("Ask user to choose .pak or .3dt before packaging")
    path = Path(c["profileFile"]); data = read(path)
    if data.get("Name") != c.get("profileName", "New Profile 0"): raise ValueError("Profile name mismatch")
    scripts = data.get("scripts", [])
    if len(scripts) != 1 or scripts[0].get("script") != "BuildCookRun": raise ValueError("Expected a supported single BuildCookRun .ulp2 profile")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup = Path(c["reports"]) / f"profile-before-{stamp}.ulp2"
    backup.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(path, backup)
    maps = [c["map"]] if fmt == "3dt" else []
    data.update(ProjectSpecified=True, ShareableProjectPath=c["targetProject"], CookedMaps=maps,
                DeployWithUnrealPak=True, GenerateChunks=False)
    data["DefaultRole"]["InitialMapName"] = ""
    scripts[0].update(project=c["targetProject"], map=maps, pak=True, manifests=False,
                      ue4exe=str(Path(c["customEngine"]) / "Engine/Binaries/Win64/UE4Editor-Cmd.exe"))
    write(Path(c["reports"]) / "profile-effective.ulp2", data); write(path, data)

def _safe_asset_file(root, relative):
    relative = str(relative).replace("\\", "/")
    if relative.startswith("/") or ":" in relative or ".." in relative.split("/"):
        raise ValueError("Dependency manifest must contain safe relative asset paths")
    result = (Path(root) / relative).resolve()
    if not result.is_relative_to(Path(root).resolve()): raise ValueError("Dependency escapes content directory")
    return result


def migration_dependencies(c):
    """Verify copied source assets byte-for-byte and derive all required cooked packages."""
    project = Path(c["project"]); target = Path(c["targetProject"])
    reports = []
    for obj in object_configs(c):
        p = Path(obj.get("migrationReport", Path(obj["reports"]) / "migration.json"))
        if p.exists(): reports.append(p)
    collection = Path(c.get("migrationReport", Path(c["reports"]) / "migration.json"))
    if collection.exists() and collection not in reports: reports.append(collection)
    if not reports: raise ValueError("Migration reports are required before packaging")
    required = set(); snapshots = {}
    for report in reports:
        data = read(report); snapshots[str(report.resolve())] = sha(report)
        groups = [("Plugins/JC_CustomAssets/Content", data.get("copiedAssets", []) + data.get("pluginDependencies", [])),
                  ("Content", data.get("migratedFiles", []))]
        for prefix, paths in groups:
            for relative in paths:
                source = _safe_asset_file(project.parent / prefix, relative)
                destination = _safe_asset_file(target.parent / prefix, relative)
                source_hash = sha(source)
                if source_hash != sha(destination): raise ValueError(f"Migrated asset changed: {relative}")
                snapshots[str(source)] = source_hash; snapshots[str(destination)] = source_hash
                required.add(target.stem + "/" + prefix + "/" + str(relative).replace("\\", "/"))
    if not required: raise ValueError("Migration dependency manifest is empty")
    return required, snapshots


def preflight(c):
    if c.get("format") not in ("pak", "3dt"): raise ValueError("Ask user to choose .pak or .3dt before packaging")
    objects = object_configs(c); snapshot = {}
    for obj in objects:
        approval(obj)
        collision = read(obj["collisionReport"])
        if not (collision.get("existingCollision") or collision.get("addedBox")):
            raise ValueError(f"Collision not checked: {obj['objectId']}")
        if collision.get("passed") is not True or collision.get("fullMotionBoundsCovered") is not True:
            raise ValueError(f"Full-motion collision coverage has not passed: {obj['objectId']}")
        if not collision.get("visibilityTracePassed"):
            raise ValueError(f"Collision trace is not verified: {obj['objectId']}")
        if collision.get("addedBox") and (collision.get("samples", 0) < 2 or not collision.get("boundsMethod")):
            raise ValueError("New collision must cover sampled full motion")
        if sha(obj["blueprintFile"]) != obj["validatedBlueprintSha256"].upper():
            raise ValueError(f"Blueprint differs from validated version: {obj['objectId']}")
        default_validation = "validate-relocated.json" if "/plugins/" in obj["blueprintFile"].replace("\\", "/").lower() else "validate.json"
        validation = Path(obj.get("validationReport", Path(obj["reports"]) / default_validation))
        if read(validation).get("passed") is not True: raise ValueError("Blueprint validation did not pass")
        pie = Path(obj.get("pieReport", Path(obj["reports"]) / "PIE/passed.json")); pie_data = read(pie)
        if pie_data.get("passed") is not True or pie_data.get("blueprintSha256", "").upper() != sha(obj["blueprintFile"]):
            raise ValueError("PIE interaction evidence is missing or stale for this Blueprint")
        if (pie_data.get("status") != "completed" or pie_data.get("fullTestPath") != "OpenModel.NoUMG.PIE" or
                any(pie_data.get(key) != value for key, value in pie_context(obj).items()) or not pie_data.get("runDirectory")):
            raise ValueError("PIE run evidence does not match this object, key, map or Blueprint")
        pie_index = Path(pie_data["runDirectory"]) / "index.json"
        check_pie_index(pie_index)
        if pie_data.get("indexSha256") != sha(pie_index): raise ValueError("PIE index changed after validation")
        paths = [obj[k] for k in ("approval", "fbx", "dccScene", "preview", "blueprintFile", "collisionReport", "samples")]
        paths += [validation, pie, pie_index]
        if obj.get("partitionManifest"): paths.append(obj["partitionManifest"])
        snapshot.update({str(Path(p).resolve()): sha(p) for p in paths})
    required, migrated = migration_dependencies(c); snapshot.update(migrated)
    for obj in objects:
        if str(Path(obj["blueprintFile"]).resolve()) not in migrated:
            raise ValueError(f"Current Blueprint is absent from migration manifest: {obj['objectId']}")
    if c["format"] == "3dt":
        map_name = c.get("map", "")
        if not map_name.startswith("/Game/"):
            raise ValueError("3dt requires a /Game/ cooked map")
        expected_map = Path(c["targetProject"]).stem + "/Content/" + map_name[6:] + ".umap"
        if expected_map not in required: raise ValueError("Requested 3dt map is absent from migration manifest")
    for obj in objects: required.update(obj.get("requiredPakPaths", []))
    required.update(c.get("requiredPakPaths", []))
    forbidden = set(c.get("forbiddenPakPaths", []))
    for obj in objects:
        forbidden.update(obj.get("forbiddenPakPaths", []))
        if c["format"] == "pak": forbidden.add("Content/" + asset_root(obj)[6:] + "/")
    return {"files": snapshot, "requiredPakPaths": sorted(required), "format": c["format"],
            "forbiddenPakPaths": sorted(forbidden),
            "targetProject": str(Path(c["targetProject"]).resolve()),
            "objects": [{"objectId": o["objectId"], "hotkey": o.get("hotkey", "N")} for o in objects]}


def package(c):
    # Every member has its own approval, collision, Blueprint and actual PIE evidence.
    snapshot = preflight(c)
    configure_profile(c)
    out = Path(c["reports"]); engine = Path(c["customEngine"])
    record = {"startUtc": datetime.now(timezone.utc).isoformat(), "status": "running", "format": c["format"],
              "inputSnapshot": snapshot, "effectiveProfileSha256": sha(c["profileFile"])}
    write(out / "run.json", record)
    try:
        run([engine / "Engine/Binaries/DotNET/AutomationTool.exe", "-nocompile", "-utf8output",
             f'-profile={c["profileFile"]}'], out / "uat.log", engine / "Engine/Binaries/DotNET")
        record.update(status="completed", exitCode=0)
    except Exception:
        record.update(status="failed"); raise
    finally:
        record["endUtc"] = datetime.now(timezone.utc).isoformat(); write(out / "run.json", record)

def verify(c):
    out = Path(c["reports"]); record = read(out / "run.json")
    if record.get("status") != "completed" or record.get("exitCode") != 0: raise ValueError("No successful build")
    if record.get("inputSnapshot") != preflight(c): raise ValueError("Build inputs changed since packaging")
    if record.get("effectiveProfileSha256") != sha(c["profileFile"]): raise ValueError("Build profile changed since packaging")
    if "BUILD SUCCESSFUL" not in (out / "uat.log").read_text(encoding="utf-8", errors="replace"): raise ValueError("UAT success marker missing")
    project = Path(c["targetProject"])
    folder = project.parent / f"Saved/StagedBuilds/WindowsNoEditor/{project.stem}/Content/Paks"
    files = list(folder.glob("*.pak"))
    if len(files) != 1: raise ValueError("Expected exactly one staged Pak")
    pak = files[0]
    if pak.stat().st_size == 0 or pak.stat().st_mtime < datetime.fromisoformat(record["startUtc"]).timestamp(): raise ValueError("Stale/empty Pak")
    exe = Path(c["customEngine"]) / "Engine/Binaries/Win64/UnrealPak.exe"
    for mode in ("Verify", "List"): run([exe, pak, "-" + mode], out / f"pak-{mode}.log")
    health = (out / "pak-Verify.log").read_text(encoding="utf-8", errors="replace")
    if not re.search(r"\bhealthy\b", health, re.IGNORECASE): raise ValueError("UnrealPak did not confirm archive health")
    listing = (out / "pak-List.log").read_text(encoding="utf-8", errors="replace")
    entries = {p.replace("\\", "/") for p in re.findall(r'"([^"\r\n]+)"\s+offset:', listing)}
    if not entries: raise ValueError("UnrealPak listing did not contain parsed archive entries")
    required = record["inputSnapshot"]["requiredPakPaths"]
    if not required: raise ValueError("Dependency manifest required")
    for path in required:
        path = path.replace("\\", "/")
        if path not in entries: raise ValueError(f"Missing packed asset: {path}")
        if Path(path).suffix in (".uasset", ".umap") and str(Path(path).with_suffix(".uexp")).replace("\\", "/") not in entries:
            raise ValueError(f"Missing cooked export payload: {path}")
    for path in record["inputSnapshot"]["forbiddenPakPaths"]:
        if any(path.replace("\\", "/") in entry for entry in entries): raise ValueError(f"Stale branch content: {path}")
    if c["format"] == "pak" and any(entry.lower().endswith(".umap") and "Demo_ExplodedAssembly_" in entry for entry in entries):
        raise ValueError("Asset-only .pak contains an exploded-assembly demo map")
    digest = sha(pak)
    if c["format"] == "3dt":
        target = pak.with_suffix(".3dt")
        if target.exists(): raise FileExistsError("Back up existing .3dt before renaming")
        pak.rename(target); pak = target
    objects = record["inputSnapshot"]["objects"]
    result = {"file": str(pak.resolve()), "bytes": pak.stat().st_size, "sha256": digest,
              "objects": objects, "objectIds": [o["objectId"] for o in objects], "progressRange": [0, 1],
              "hotkey": objects[0]["hotkey"] if len({o["hotkey"] for o in objects}) == 1 else None,
              "hashVerificationPassed": True, "allCookedExportPayloadsPresent": True, "frontendRuntimeTested": False}
    write(out / "delivery.json", result); print(json.dumps(result, ensure_ascii=False, indent=2))

def main():
    stages = ("build-plugin", "import", "controls", "free-camera", "collision", "validate", "validate-relocated", "pie", "prepare")
    p = argparse.ArgumentParser(); p.add_argument("command", choices=("discover", "verify-dcc", "check-approval", "materialize", "configure-profile", "package", "verify") + tuple("ue-"+s for s in stages)); p.add_argument("config", nargs="?")
    a = p.parse_args()
    if a.command == "discover": print(json.dumps(discover(), ensure_ascii=False, indent=2)); return
    if not a.config: p.error("config JSON required")
    c = load_config(a.config)
    if a.command.startswith("ue-"): ue(c, a.command[3:]); return
    {"check-approval": approval, "verify-dcc": verify_dcc, "materialize": materialize, "configure-profile": configure_profile,
     "package": package, "verify": verify}[a.command](c)

if __name__ == "__main__": main()
