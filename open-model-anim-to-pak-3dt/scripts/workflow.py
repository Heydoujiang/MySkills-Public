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
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]

def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))

def write(path, data):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

def sha(path):
    with open(path, "rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest().upper()

def run(argv, log, cwd=None):
    log = Path(log); log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("w", encoding="utf-8") as out:
        p = subprocess.run([str(x) for x in argv], cwd=cwd, stdout=out,
                           stderr=subprocess.STDOUT, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if p.returncode:
        raise RuntimeError(f"Exit {p.returncode}; see {log}")

def approval(config):
    a = read(config["approval"])
    if a.get("status") != "approved" or not a.get("userConfirmation", "").strip():
        raise ValueError("DCC preview approval is pending; obtain actual user confirmation")
    for key, field in (("fbxSha256", "fbx"), ("mayaFileSha256", "dccScene"), ("previewSha256", "preview")):
        if a.get(key, "").upper() != sha(config[field]):
            raise ValueError(f"Approved revision changed: {field}")

def discover():
    names = {"maya": ("mayapy.exe", "mayapy"), "blender": ("blender.exe", "blender"),
             "houdini": ("hython.exe", "hython"), "3dsmax": ("3dsmaxbatch.exe",)}
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
    return {"candidates": {k: sorted(v) for k, v in found.items()},
            "default": "maya" if found["maya"] else next((k for k, v in found.items() if v), None),
            "note": "Executable discovery only; verify license, startup and import/export support."}

def materialize(c):
    name = c["objectId"]; key = c.get("hotkey", "N")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name): raise ValueError("objectId must be an ASCII asset identifier")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", key): raise ValueError("hotkey must be an Unreal EKeys identifier")
    project = Path(c["project"])
    destination = project.parent / "Plugins/OpenModelBuilder"
    if destination.exists(): raise FileExistsError(f"Reconcile or back up existing plugin first: {destination}")
    shutil.copytree(ROOT / "assets/OpenModelBuilder", destination)
    for p in destination.rglob("*"):
        if p.suffix in (".cpp", ".h", ".inl", ".cs"):
            text = p.read_text(encoding="utf-8-sig")
            text = text.replace("/Game/OpenModel", f"/Game/{name}").replace("SK_OpenModel", f"SK_{name}")
            text = text.replace("OpenModel_Gun", f"OpenModel_{name}").replace("EKeys::N", f"EKeys::{key}")
            p.write_text(text, encoding="utf-8")
    # Editor-only builder: no native runtime parent required by the generated actor.
    data = read(project); plugins = data.setdefault("Plugins", [])
    existing = next((p for p in plugins if p["Name"] == "OpenModelBuilder"), None)
    if existing: existing["Enabled"] = True
    else: plugins.append({"Name": "OpenModelBuilder", "Enabled": True})
    shutil.copy2(project, project.with_suffix(".uproject.before-explode")); write(project, data)
    print(destination)

def ue(c, stage):
    approval(c)
    project = Path(c["project"]); out = Path(c["reports"]); name = c["objectId"]
    root = f"/Game/{name}"
    if stage == "build-plugin":
        run([Path(c["engine"]) / "Engine/Binaries/DotNET/UnrealBuildTool.exe", "UE4Editor", "Win64", "Development",
             f"-Project={project}", "-WaitMutex", "-NoHotReloadFromIDE", "-2019", "-gather"], out / "build-plugin.log")
        return
    args = [Path(c["engine"]) / "Engine/Binaries/Win64/UE4Editor-Cmd.exe", project, "-unattended", "-nop4", "-nosound", "-nullrhi",
            f"-ShaderWorkingDir={out / 'ShaderWork'}", f"-abslog={out / (stage + '.log')}"]
    if stage == "import":
        args += ["-run=OpenModelBuild", f"-Root={root}", f"-Name={name}", f'-Fbx={c["fbx"]}']
        if c.get("importOnly"): args += ["-ImportOnly"]
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
        args += ["-run=OpenModelValidate", "-NoUMG", f"-Root={assetroot}", f"-BPName={bp}",
                 f'-Samples={c["samples"]}', f'-Report={out / (stage + ".json")}']
    elif stage == "prepare":
        fmt = c.get("format")
        if fmt not in ("pak", "3dt"): raise ValueError("Ask user for output format")
        target = Path(c["targetProject"])
        if target.resolve() == project.resolve(): raise ValueError("Prepare must use an isolated work project and a distinct preset target")
        args += ["-run=OpenModelNoUMG", f'-CustomRoot={c["customEngine"]}', f"-TargetProject={target}", f'-Report={out / "migration.json"}']
        if fmt == "3dt": args += ["-PreparePackage", f'-Destination={target.parent / "Content"}', f'-Map={c["map"]}']
        else:
            args += ["-PreparePakBranch", f'-Destination={target.parent / "Plugins/JC_CustomAssets/Content"}', f'-Thumbnail={c["thumbnail"]}']
    else: raise ValueError(stage)
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

def package(c):
    # Authoring approval and collision are independent prerequisites.
    approval(c)
    collision = read(c["collisionReport"])
    if not (collision.get("existingCollision") or collision.get("addedBox")): raise ValueError("Collision not checked")
    if sha(c["blueprintFile"]) != c["validatedBlueprintSha256"].upper(): raise ValueError("Blueprint differs from validated version")
    configure_profile(c)
    out = Path(c["reports"]); engine = Path(c["customEngine"])
    record = {"startUtc": datetime.now(timezone.utc).isoformat(), "status": "running", "format": c["format"]}
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
    if "BUILD SUCCESSFUL" not in (out / "uat.log").read_text(encoding="utf-8", errors="replace"): raise ValueError("UAT success marker missing")
    project = Path(c["targetProject"])
    folder = project.parent / f"Saved/StagedBuilds/WindowsNoEditor/{project.stem}/Content/Paks"
    files = list(folder.glob("*.pak"))
    if len(files) != 1: raise ValueError("Expected exactly one staged Pak")
    pak = files[0]
    if pak.stat().st_size == 0 or pak.stat().st_mtime < datetime.fromisoformat(record["startUtc"]).timestamp(): raise ValueError("Stale/empty Pak")
    exe = Path(c["customEngine"]) / "Engine/Binaries/Win64/UnrealPak.exe"
    for mode in ("Verify", "List"): run([exe, pak, "-" + mode], out / f"pak-{mode}.log")
    listing = (out / "pak-List.log").read_text(encoding="utf-8", errors="replace")
    required = c["requiredPakPaths"]
    if not required: raise ValueError("Dependency manifest required")
    for path in required:
        if path not in listing: raise ValueError(f"Missing packed asset: {path}")
    for path in c.get("forbiddenPakPaths", []):
        if path in listing: raise ValueError(f"Stale branch content: {path}")
    digest = sha(pak)
    if c["format"] == "3dt":
        target = pak.with_suffix(".3dt")
        if target.exists(): raise FileExistsError("Back up existing .3dt before renaming")
        pak.rename(target); pak = target
    result = {"file": str(pak.resolve()), "bytes": pak.stat().st_size, "sha256": digest,
              "hotkey": c.get("hotkey", "N"), "hashVerificationPassed": True, "frontendRuntimeTested": False}
    write(out / "delivery.json", result); print(json.dumps(result, ensure_ascii=False, indent=2))

def main():
    stages = ("build-plugin", "import", "controls", "free-camera", "collision", "validate", "validate-relocated", "prepare")
    p = argparse.ArgumentParser(); p.add_argument("command", choices=("discover", "check-approval", "materialize", "configure-profile", "package", "verify") + tuple("ue-"+s for s in stages)); p.add_argument("config", nargs="?")
    a = p.parse_args()
    if a.command == "discover": print(json.dumps(discover(), ensure_ascii=False, indent=2)); return
    if not a.config: p.error("config JSON required")
    c = read(a.config)
    if a.command.startswith("ue-"): ue(c, a.command[3:]); return
    {"check-approval": approval, "materialize": materialize, "configure-profile": configure_profile,
     "package": package, "verify": verify}[a.command](c)

if __name__ == "__main__": main()
