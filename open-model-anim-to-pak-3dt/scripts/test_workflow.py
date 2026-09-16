"""Side-effect-free regression tests using disposable projects and fake tool output."""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from datetime import datetime, timezone, timedelta
import workflow as w


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.c = {"reports": str(self.root / "reports"), "objectId": "Car", "hotkey": "K",
                  "targetProject": str(self.root / "Preset/Preset.uproject"),
                  "customEngine": str(self.root / "Engine"), "map": "/Game/Car/NoUMG/Demo"}
        w.write(self.c["targetProject"], {"FileVersion": 3})

    def tearDown(self):
        self.temp.cleanup()

    def profile(self):
        self.c["profileFile"] = str(self.root / "profile.ulp2")
        w.write(self.c["profileFile"], {"Name": "New Profile 0", "CookedMaps": ["/Game/Old"],
            "DefaultRole": {"InitialMapName": "/Game/Old"}, "Compressed": True,
            "scripts": [{"script": "BuildCookRun", "clientconfig": ["Shipping"], "map": ["/Game/Old"]}]})

    def test_format_is_required_without_mutating_profile(self):
        self.profile(); before = w.sha(self.c["profileFile"])
        with self.assertRaises(ValueError): w.configure_profile(self.c)
        self.assertEqual(before, w.sha(self.c["profileFile"]))

    def test_switch_branches_preserves_unrelated_profile_settings(self):
        self.profile()
        for fmt, maps in (("3dt", [self.c["map"]]), ("pak", [])):
            self.c["format"] = fmt; w.configure_profile(self.c)
            profile = w.read(self.c["profileFile"])
            self.assertEqual(maps, profile["CookedMaps"])
            self.assertEqual(maps, profile["scripts"][0]["map"])
            self.assertTrue(profile["Compressed"])
            self.assertTrue(profile["DeployWithUnrealPak"])
            self.assertEqual(["Shipping"], profile["scripts"][0]["clientconfig"])
        self.assertEqual(2, len(list(Path(self.c["reports"]).glob("profile-before-*.ulp2"))))

    def test_materialize_car_and_nondefault_key_preserves_project(self):
        self.c["project"] = str(self.root / "Work/Work.uproject")
        w.write(self.c["project"], {"FileVersion": 3, "Description": "keep", "Plugins": [{"Name": "Other", "Enabled": True}]})
        with contextlib.redirect_stdout(io.StringIO()): w.materialize(self.c)
        p = w.read(self.c["project"])
        self.assertEqual("keep", p["Description"])
        self.assertTrue(any(x["Name"] == "Other" for x in p["Plugins"]))
        plugin = Path(self.c["project"]).parent / "Plugins/OpenModelBuilder/Source/OpenModelBuilder/Private"
        control = (plugin / "OpenModelNoUMG.inl").read_text(encoding="utf-8")
        self.assertIn("EKeys::K", control)
        self.assertNotIn("/Game/OpenModel", control)
        assets = (plugin / "OpenModelPakBranch.inl").read_text(encoding="utf-8")
        self.assertIn("/Exhibition/OpenModel/OpenModel_Car", assets)
        self.assertNotIn("OpenModel_Gun", assets)
        with self.assertRaises(FileExistsError): w.materialize(self.c)

    def test_stale_approval_rejected(self):
        data = {"status": "approved", "userConfirmation": "Synthetic test confirmation, not production approval"}
        for field, key in (("fbx", "fbxSha256"), ("dccScene", "mayaFileSha256"), ("preview", "previewSha256")):
            f = self.root / field; f.write_bytes(b"test"); self.c[field] = str(f); data[key] = w.sha(f)
        self.c["approval"] = str(self.root / "approval.json"); w.write(self.c["approval"], data)
        w.approval(self.c)
        Path(self.c["preview"]).write_bytes(b"changed")
        with self.assertRaises(ValueError): w.approval(self.c)

    def prepare_verify(self, fmt="3dt"):
        self.c.update(format=fmt, requiredPakPaths=["Preset/Content/Car.uasset"])
        out = Path(self.c["reports"]); w.write(out / "run.json", {"status": "completed", "exitCode": 0,
            "startUtc": (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()})
        (out / "uat.log").write_text("BUILD SUCCESSFUL", encoding="utf-8")
        pak = self.root / "Preset/Saved/StagedBuilds/WindowsNoEditor/Preset/Content/Paks/Preset.pak"
        pak.parent.mkdir(parents=True); pak.write_bytes(b"synthetic pak")
        return pak

    def fake_run(self, args, log, cwd=None):
        Path(log).write_text("Preset/Content/Car.uasset" if args[-1] == "-List" else "healthy", encoding="utf-8")

    def test_verified_3dt_renames_without_changing_bytes(self):
        pak = self.prepare_verify(); expected = w.sha(pak)
        with patch.object(w, "run", self.fake_run), contextlib.redirect_stdout(io.StringIO()): w.verify(self.c)
        self.assertFalse(pak.exists()); self.assertEqual(expected, w.sha(pak.with_suffix(".3dt")))
        self.assertEqual("K", w.read(Path(self.c["reports"]) / "delivery.json")["hotkey"])

    def test_missing_dependency_does_not_rename(self):
        pak = self.prepare_verify(); self.c["requiredPakPaths"].append("Missing.uasset")
        with patch.object(w, "run", self.fake_run), self.assertRaises(ValueError): w.verify(self.c)
        self.assertTrue(pak.exists()); self.assertFalse(pak.with_suffix(".3dt").exists())

    def test_stale_pak_does_not_invoke_tools(self):
        pak = self.prepare_verify(); w.write(Path(self.c["reports"]) / "run.json", {"status": "completed", "exitCode": 0,
            "startUtc": (datetime.now(timezone.utc) + timedelta(seconds=20)).isoformat()})
        with patch.object(w, "run") as execute, self.assertRaises(ValueError): w.verify(self.c)
        execute.assert_not_called(); self.assertTrue(pak.exists())


if __name__ == "__main__": unittest.main()
