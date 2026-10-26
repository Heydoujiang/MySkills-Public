"""Disposable fixtures and fake tools; never open production models or projects."""
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
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.c = dict(reports=str(self.root/'reports/Car'), objectId='Car', hotkey='K',
                      project=str(self.root/'Work/Work.uproject'), engine=str(self.root/'Engine'),
                      targetProject=str(self.root/'Preset/Preset.uproject'), customEngine=str(self.root/'Engine'),
                      map='/Game/Car/NoUMG/Demo')
        w.write(self.c['project'], dict(FileVersion=3, Description='keep', Plugins=[dict(Name='Other', Enabled=True)]))
        w.write(self.c['targetProject'], dict(FileVersion=3))

    def tearDown(self): self.temp.cleanup()

    def file(self, path, content=b'fixture'):
        path=Path(path); path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(content); return path

    def profile(self):
        self.c['profileFile']=str(self.root/'profile.ulp2')
        w.write(self.c['profileFile'], dict(Name='New Profile 0', CookedMaps=['/Game/Old'],
            DefaultRole=dict(InitialMapName='/Game/Old'), Compressed=True,
            scripts=[dict(script='BuildCookRun', clientconfig=['Shipping'], map=['/Game/Old'])]))

    def approved(self, c=None, legacy=False):
        c=self.c if c is None else c
        data=dict(status='approved', userConfirmation='Synthetic test confirmation, never production approval')
        for field,key in [('fbx','fbxSha256'),('dccScene','mayaFileSha256' if legacy else 'dccSceneSha256'),('preview','previewSha256')]:
            p=self.file(Path(c['reports'])/field); c[field]=str(p); data[key]=w.sha(p)
        c['approval']=str(Path(c['reports'])/'approval.json'); w.write(c['approval'],data); return c

    def ready(self,c=None):
        c=self.approved(c); out=Path(c['reports']); name=c['objectId']
        c['samples']=str(self.file(out/'samples.json',b'{}'))
        c['blueprintFile']=str(self.file(Path(c['project']).parent/f'Content/{name}/BP.uasset'))
        c['validatedBlueprintSha256']=w.sha(c['blueprintFile']); c['collisionReport']=str(out/'collision.json')
        w.write(c['collisionReport'],dict(passed=True,fullMotionBoundsCovered=True,addedBox=True,samples=661,boundsMethod='full motion',visibilityTracePassed=True))
        w.write(out/'validate.json',dict(passed=True))
        run_dir=out/'PIE/fixture-run'; w.write(run_dir/'index.json',self.success_index())
        w.write(out/'PIE/passed.json',dict(w.pie_context(c),passed=True,status='completed',fullTestPath='OpenModel.NoUMG.PIE',
                runDirectory=str(run_dir.resolve()),indexSha256=w.sha(run_dir/'index.json')))
        self.file(Path(c['targetProject']).parent/f'Content/{name}/BP.uasset')
        w.write(out/'migration.json',dict(migratedFiles=[f'{name}/BP.uasset']))
        return c

    def success_index(self,test_name='OpenModel.NoUMG.PIE',state='Success'):
        # Schema copied from an actual UE4.26 Automation export, with synthetic contents.
        return dict(succeeded=1,succeededWithWarnings=0,failed=0,notRun=0,
                    tests=[dict(fullTestPath=test_name,state=state,errors=0,warnings=0,entries=[])])

    def test_format_required_without_mutating_profile(self):
        self.profile(); before=w.sha(self.c['profileFile'])
        with self.assertRaises(ValueError): w.configure_profile(self.c)
        self.assertEqual(before,w.sha(self.c['profileFile']))

    def test_profile_branch_switch_preserves_other_settings(self):
        self.profile()
        for fmt,maps in [('3dt',[self.c['map']]),('pak',[])]:
            self.c['format']=fmt; w.configure_profile(self.c); data=w.read(self.c['profileFile'])
            self.assertEqual(maps,data['CookedMaps']); self.assertEqual(maps,data['scripts'][0]['map'])
            self.assertTrue(data['Compressed']); self.assertTrue(data['DeployWithUnrealPak']); self.assertFalse(data['GenerateChunks'])
            self.assertFalse(data['scripts'][0]['manifests']); self.assertEqual(['Shipping'],data['scripts'][0]['clientconfig'])
        self.assertEqual(2,len(list(Path(self.c['reports']).glob('profile-before-*.ulp2'))))

    def test_generic_materialize_never_rewrites_template(self):
        with contextlib.redirect_stdout(io.StringIO()): w.materialize(self.c)
        p=w.read(self.c['project']); self.assertEqual('keep',p['Description']); self.assertTrue(any(x['Name']=='Other' for x in p['Plugins']))
        source=w.ROOT/'assets/OpenModelBuilder'; dest=Path(self.c['project']).parent/'Plugins/OpenModelBuilder'
        for file in source.rglob('*'):
            if file.is_file(): self.assertEqual(w.sha(file),w.sha(dest/file.relative_to(source)))
        with self.assertRaises(FileExistsError): w.materialize(self.c)

    def test_generic_and_legacy_approvals(self):
        for legacy in (False,True): self.approved(legacy=legacy); w.approval(self.c)
        Path(self.c['preview']).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'preview'): w.approval(self.c)

    def test_conflicting_legacy_hash_is_rejected(self):
        self.approved(); data=w.read(self.c['approval']); data['mayaFileSha256']='bad'; w.write(self.c['approval'],data)
        with self.assertRaisesRegex(ValueError,'legacy'): w.approval(self.c)

    def bundle(self):
        self.ready(); b=dict(self.c); b.update(objectId='Room',reports=str(self.root/'reports/Room'),hotkey='N'); self.ready(b)
        paths=[]
        for item in (self.c,b):
            path=self.root/(item['objectId']+'.json'); w.write(path,item); paths.append(path.name)
        result={k:self.c[k] for k in ('project','engine','customEngine','targetProject')}
        result.update(objects=paths,_configDir=str(self.root),reports=str(self.root/'bundle'),format='pak')
        return result,b

    def test_every_model_requires_independent_approval(self):
        bundle,b=self.bundle(); w.approval(bundle); Path(b['dccScene']).write_bytes(b'revised')
        with patch.object(w,'run') as execute,self.assertRaisesRegex(ValueError,'dccScene'): w.ue(bundle,'controls')
        execute.assert_not_called()

    def test_duplicate_ids_fail(self):
        bundle,b=self.bundle(); data=w.read(self.root/'Room.json'); data['objectId']='car'; w.write(self.root/'Room.json',data)
        with self.assertRaisesRegex(ValueError,'unique'): w.object_configs(bundle)

    def test_all_model_migrated_assets_are_checked(self):
        bundle,b=self.bundle(); snapshot=w.preflight(bundle)
        self.assertEqual(['Car','Room'],[x['objectId'] for x in snapshot['objects']]); self.assertEqual(2,len(snapshot['requiredPakPaths']))
        Path(b['targetProject']).parent.joinpath('Content/Room/BP.uasset').write_bytes(b'stale target')
        with self.assertRaisesRegex(ValueError,'Migrated asset changed'): w.preflight(bundle)

    def test_stale_pie_prevents_package(self):
        self.ready(); self.profile(); self.c['format']='pak'
        w.write(Path(self.c['reports'])/'PIE/passed.json',dict(passed=True,blueprintSha256='wrong'))
        with patch.object(w,'run') as execute,self.assertRaisesRegex(ValueError,'PIE'): w.package(self.c)
        execute.assert_not_called()

    def test_controls_receive_runtime_id_and_hotkey(self):
        self.approved()
        with patch.object(w,'run') as execute: w.ue(self.c,'controls')
        args=execute.call_args.args[0]
        self.assertIn('-ObjectId=Car',args); self.assertIn('-Hotkey=K',args); self.assertIn('-Root=/Game/Car',args)

    def test_validate_has_only_one_root(self):
        self.approved(); self.c['samples']='samples.json'
        with patch.object(w,'run') as execute: w.ue(self.c,'validate-relocated')
        self.assertEqual(['-Root=/JC_CustomAssets/ObjectLibrary/Exhibition/OpenModel/OpenModel_Car'],[x for x in execute.call_args.args[0] if str(x).startswith('-Root=')])

    def test_validation_override_is_written_and_consumed(self):
        self.ready(); self.c['format']='pak'; custom=self.root/'custom/evidence/validation.json'; self.c['validationReport']=str(custom)
        w.write(Path(self.c['reports'])/'validate.json',dict(passed=False))
        def validate(argv,log,cwd=None):
            report=Path(next(str(x)[8:] for x in argv if str(x).startswith('-Report=')))
            self.assertEqual(custom,report); self.assertTrue(report.parent.is_dir()); w.write(report,dict(passed=True))
        for stage in ('validate','validate-relocated'):
            with patch.object(w,'run',validate): w.ue(self.c,stage)
            self.assertIn(str(custom.resolve()),w.preflight(self.c)['files'])

    def test_pie_override_is_written_and_consumed(self):
        self.ready(); self.c['format']='pak'; out=Path(self.c['reports']); custom=self.root/'custom/evidence/interaction.json'; self.c['pieReport']=str(custom)
        w.write(out/'PIE/passed.json',dict(passed=False))
        def pie(argv,log,cwd=None):
            run_dir=Path(next(str(x).split('=',1)[1] for x in argv if str(x).startswith('-ReportExportPath=')))
            self.assertEqual((out/'PIE/runs').resolve(),run_dir.parent.resolve())
            w.write(run_dir/'index.json',self.success_index())
        with patch.object(w,'run',pie): w.ue(self.c,'pie')
        self.assertEqual(self.c['validatedBlueprintSha256'],w.read(custom)['blueprintSha256'])
        self.assertIn(str(custom.resolve()),w.preflight(self.c)['files'])

    def test_pie_exit_zero_without_new_report_cannot_reuse_old_success(self):
        self.ready(); self.c['format']='pak'; out=Path(self.c['reports']); previous=w.read(out/'PIE/passed.json')
        w.write(out/'PIE/index.json',self.success_index())
        with patch.object(w,'run'),self.assertRaisesRegex(RuntimeError,'own index.json'): w.ue(self.c,'pie')
        result=w.read(out/'PIE/passed.json'); self.assertFalse(result['passed']); self.assertEqual('failed',result['status'])
        self.assertEqual(previous,w.read(Path(result['runDirectory'])/'previous-passed.json'))
        self.assertTrue((out/'PIE/index.json').exists())
        with self.assertRaisesRegex(ValueError,'PIE interaction evidence'): w.preflight(self.c)

    def test_pie_wrong_test_or_state_cannot_produce_pass(self):
        self.ready(); out=Path(self.c['reports'])
        for test_name,state in [('Another.Test','Success'),('OpenModel.NoUMG.PIE','NotRun')]:
            def pie(argv,log,cwd=None):
                run_dir=Path(next(str(x).split('=',1)[1] for x in argv if str(x).startswith('-ReportExportPath=')))
                w.write(run_dir/'index.json',self.success_index(test_name,state))
            with patch.object(w,'run',pie),self.assertRaises(RuntimeError): w.ue(self.c,'pie')
            self.assertFalse(w.read(out/'PIE/passed.json')['passed'])
        self.assertEqual(2,len(list((out/'PIE/runs').iterdir())))

    def test_pie_binds_unique_run_key_map_and_blueprint_revision(self):
        self.ready(); self.c['format']='pak'; out=Path(self.c['reports']); runs=[]
        def pie(argv,log,cwd=None):
            run_dir=Path(next(str(x).split('=',1)[1] for x in argv if str(x).startswith('-ReportExportPath=')))
            runs.append(run_dir); w.write(run_dir/'index.json',self.success_index())
        for _ in range(2):
            with patch.object(w,'run',pie): w.ue(self.c,'pie')
        self.assertEqual(2,len(set(runs))); record=w.read(out/'PIE/passed.json')
        self.assertEqual(str(runs[-1].resolve()),record['runDirectory']); self.assertEqual(self.c['map'],record['map'])
        self.assertEqual('K',record['hotkey']); w.preflight(self.c)
        self.c['hotkey']='J'
        with self.assertRaisesRegex(ValueError,'key, map or Blueprint'): w.preflight(self.c)

    def test_pie_concurrent_blueprint_change_fails(self):
        self.ready(); out=Path(self.c['reports'])
        def pie(argv,log,cwd=None):
            run_dir=Path(next(str(x).split('=',1)[1] for x in argv if str(x).startswith('-ReportExportPath=')))
            w.write(run_dir/'index.json',self.success_index()); Path(self.c['blueprintFile']).write_bytes(b'edited during test')
        with patch.object(w,'run',pie),self.assertRaisesRegex(RuntimeError,'changed during PIE'): w.ue(self.c,'pie')
        self.assertFalse(w.read(out/'PIE/passed.json')['passed'])

    def test_prepare_respects_migration_override(self):
        self.ready(); self.c.update(format='pak',thumbnail=str(self.root/'thumbnail.png'),migrationReport=str(self.root/'custom/migration.json'))
        def prepare(argv,log,cwd=None):
            report=Path(next(str(x)[8:] for x in argv if str(x).startswith('-Report=')))
            self.assertEqual(Path(self.c['migrationReport']),report); self.assertTrue(report.parent.is_dir())
            w.write(report,dict(migratedFiles=['Car/BP.uasset']))
        with patch.object(w,'run',prepare): w.ue(self.c,'prepare')
        self.assertIn(str(Path(self.c['migrationReport']).resolve()),w.preflight(self.c)['files'])

    def test_collection_prepare_writes_and_reads_migration_override(self):
        bundle,other=self.bundle(); custom=self.root/'custom/collection.json'
        bundle.update(format='3dt',map='/Game/Collection/Demo',collectionManifest=str(self.root/'actors.json'),migrationReport=str(custom))
        def prepare(argv,log,cwd=None):
            self.assertIn('-Report='+str(custom),argv); self.assertTrue(custom.parent.is_dir())
            w.write(custom,dict(businessActorCount=2,actors=[dict(objectId='Car'),dict(objectId='Room')]))
        with patch.object(w,'run',prepare): w.ue(bundle,'prepare')
        self.assertEqual(2,w.read(custom)['businessActorCount'])

    def prepare_verify(self,fmt='3dt'):
        self.ready(); self.profile(); self.c['format']=fmt; w.configure_profile(self.c); out=Path(self.c['reports'])
        if fmt=='3dt':
            relative=self.c['map'][6:]+'.umap'
            for project in (self.c['project'],self.c['targetProject']): self.file(Path(project).parent/'Content'/relative)
            migration=w.read(out/'migration.json'); migration['migratedFiles'].append(relative); w.write(out/'migration.json',migration)
        w.write(out/'run.json',dict(status='completed',exitCode=0,startUtc=(datetime.now(timezone.utc)-timedelta(seconds=5)).isoformat(),
                inputSnapshot=w.preflight(self.c),effectiveProfileSha256=w.sha(self.c['profileFile'])))
        (out/'uat.log').write_text('BUILD SUCCESSFUL',encoding='utf-8')
        return self.file(self.root/'Preset/Saved/StagedBuilds/WindowsNoEditor/Preset/Content/Paks/Preset.pak',b'synthetic pak')

    def fake_run(self,args,log,cwd=None,**kwargs):
        listing='\n'.join(f'LogPakFile: Display: "{p}" offset: 0, size: 4 bytes.' for p in ['Preset/Content/Car/BP.uasset','Preset/Content/Car/BP.uexp','Preset/Content/Car/NoUMG/Demo.umap','Preset/Content/Car/NoUMG/Demo.uexp'])
        Path(log).write_text(listing if args[-1]=='-List' else 'Pak file healthy',encoding='utf-8')

    def test_verified_3dt_preserves_bytes(self):
        pak=self.prepare_verify(); expected=w.sha(pak)
        with patch.object(w,'run',self.fake_run),contextlib.redirect_stdout(io.StringIO()): w.verify(self.c)
        self.assertFalse(pak.exists()); self.assertEqual(expected,w.sha(pak.with_suffix('.3dt')))
        result=w.read(Path(self.c['reports'])/'delivery.json'); self.assertEqual('K',result['hotkey']); self.assertTrue(result['allCookedExportPayloadsPresent'])

    def test_missing_uexp_never_renames(self):
        pak=self.prepare_verify()
        def missing(args,log,cwd=None): Path(log).write_text('"Preset/Content/Car/BP.uasset" offset: 0' if args[-1]=='-List' else 'healthy',encoding='utf-8')
        with patch.object(w,'run',missing),self.assertRaisesRegex(ValueError,'payload'): w.verify(self.c)
        self.assertTrue(pak.exists())

    def test_substring_asset_match_cannot_pass(self):
        self.prepare_verify()
        def similar(args,log,cwd=None): Path(log).write_text('"WrongPreset/Content/Car/BP.uasset" offset: 0' if args[-1]=='-List' else 'healthy',encoding='utf-8')
        with patch.object(w,'run',similar),self.assertRaisesRegex(ValueError,'Missing packed'): w.verify(self.c)

    def test_changed_inputs_prevent_delivery(self):
        pak=self.prepare_verify(); Path(self.c['preview']).write_bytes(b'changed')
        with patch.object(w,'run') as execute,self.assertRaises(ValueError): w.verify(self.c)
        execute.assert_not_called(); self.assertTrue(pak.exists())

    def test_stale_pak_does_not_invoke_tools(self):
        pak=self.prepare_verify(); path=Path(self.c['reports'])/'run.json'; data=w.read(path)
        data['startUtc']=(datetime.now(timezone.utc)+timedelta(seconds=20)).isoformat(); w.write(path,data)
        with patch.object(w,'run') as execute,self.assertRaisesRegex(ValueError,'Stale'): w.verify(self.c)
        execute.assert_not_called(); self.assertTrue(pak.exists())

    def test_missing_collection_map_prevents_packaging(self):
        self.ready(); self.c['format']='3dt'
        with self.assertRaisesRegex(ValueError,'map is absent'): w.preflight(self.c)

    def test_model_blueprint_cannot_be_omitted_from_migration(self):
        self.ready(); self.c['format']='pak'
        for p in (self.c['project'],self.c['targetProject']): self.file(Path(p).parent/'Content/Car/Unrelated.uasset')
        w.write(Path(self.c['reports'])/'migration.json',dict(migratedFiles=['Car/Unrelated.uasset']))
        with self.assertRaisesRegex(ValueError,'Blueprint is absent'): w.preflight(self.c)

    def test_pak_only_rejects_actual_demo_maps_not_named_regular_assets(self):
        pak=self.prepare_verify('pak'); out=Path(self.c['reports'])
        # Isolate archive branch policy from the separate migration/approval checks.
        snapshot=w.read(out/'run.json')['inputSnapshot']; snapshot['forbiddenPakPaths']=[]
        record=w.read(out/'run.json'); record['inputSnapshot']=snapshot; w.write(out/'run.json',record)
        def named_asset(args,log,cwd=None):
            self.fake_run(args,log,cwd)
            if args[-1]=='-List':
                with Path(log).open('a',encoding='utf-8') as stream: stream.write('\n"Preset/Content/Demo_ExplodedAssembly_Label.uasset" offset: 0')
        with patch.object(w,'preflight',return_value=snapshot),patch.object(w,'run',named_asset),contextlib.redirect_stdout(io.StringIO()): w.verify(self.c)
        self.assertTrue(pak.exists()); self.assertFalse(pak.with_suffix('.3dt').exists())

    def test_partition_groups_import_sequentially_share_skeleton(self):
        exe,source,args=self.partition(); out=Path(self.c['reports']); manifest=Path(self.c['partitionManifest'])
        w.write(manifest,dict(groups=[dict(fbx=str(source),assetName='SK_Car'),dict(fbx=str(source),assetName='SK_Car_Group02')]))
        calls=[]
        def importer(argv,log,cwd=None):
            calls.append(argv); name=next(x[6:] for x in argv if str(x).startswith('-Name='))
            names=['SK_'+name]+(['SK_Car_Skeleton','SK_Car_Anim'] if len(calls)==1 else [])
            for asset in names: self.file(Path(self.c['project']).parent/f'Content/Car/Model/{asset}.uasset')
            Path(next(str(x)[8:] for x in argv if str(x).startswith('-abslog='))).write_text('OPENMODEL_BUILD_OK' if len(calls)==1 else 'OPENMODEL_PARTITION_IMPORTED')
        with patch.object(w,'run',importer): w._partition_import(self.c,args,out,'/Game/Car')
        self.assertEqual(2,len(calls)); self.assertNotIn('-MeshOnly',calls[0]); self.assertIn('-MeshOnly',calls[1])
        self.assertIn('-SharedSkeleton=/Game/Car/Model/SK_Car_Skeleton.SK_Car_Skeleton',calls[1])

    def partition(self):
        self.approved(); exe=self.file(self.root/'ue.exe'); source=self.file(self.root/'part.fbx'); manifest=self.root/'partition.json'
        w.write(manifest,dict(groups=[dict(fbx=str(source),assetName='SK_Car')]))
        self.c['partitionManifest']=str(manifest)
        return exe,source,[str(exe),'-Name=Car','-Fbx=unused']

    def test_partition_restart_requires_matching_revision_receipt(self):
        exe,source,args=self.partition(); out=Path(self.c['reports'])
        def importer(argv,log,cwd=None):
            for name in ('SK_Car','SK_Car_Skeleton','SK_Car_Anim'): self.file(Path(self.c['project']).parent/f'Content/Car/Model/{name}.uasset')
            Path(next(str(x)[8:] for x in argv if str(x).startswith('-abslog='))).write_text('OPENMODEL_BUILD_OK')
        with patch.object(w,'run',importer): w._partition_import(self.c,args,out,'/Game/Car')
        with patch.object(w,'run') as execute,contextlib.redirect_stdout(io.StringIO()):
            w._partition_import(self.c,args,out,'/Game/Car'); execute.assert_not_called()
        source.write_bytes(b'new revision')
        with patch.object(w,'run') as execute,self.assertRaisesRegex(FileExistsError,'matching import receipt'): w._partition_import(self.c,args,out,'/Game/Car')
        execute.assert_not_called()

    def test_old_asset_and_success_log_cannot_resume(self):
        exe,source,args=self.partition(); out=Path(self.c['reports'])
        self.file(Path(self.c['project']).parent/'Content/Car/Model/SK_Car.uasset'); self.file(out/'import-group-01.log',b'OPENMODEL_BUILD_OK')
        with patch.object(w,'run') as execute,self.assertRaises(FileExistsError): w._partition_import(self.c,args,out,'/Game/Car')
        execute.assert_not_called()

    def test_dcc_priority_and_explicit_failure(self):
        exes={k:[str(self.file(self.root/(k+'.exe')))] for k in w.DCC_PRIORITY}; cfg=dict(reports=str(self.root/'probe'),dcc='3dsmax')
        with patch.object(w,'discover',return_value=dict(candidates=exes)),patch.object(w,'run',side_effect=RuntimeError('license unavailable')) as execute:
            with self.assertRaisesRegex(RuntimeError,'Selected DCC'): w.verify_dcc(cfg)
        self.assertEqual(1,execute.call_count); self.assertEqual(exes['3dsmax'][0],execute.call_args.args[0][0]); cfg.pop('dcc')
        with patch.object(w,'discover',return_value=dict(candidates=exes)),patch.object(w,'run',side_effect=RuntimeError('license unavailable')) as execute:
            with self.assertRaisesRegex(RuntimeError,'other DCCs have not been exhausted'): w.verify_dcc(cfg)
        self.assertEqual([exes[k][0] for k in ['maya','3dsmax','blender','houdini']],[call.args[0][0] for call in execute.call_args_list])
        report=w.read(Path(cfg['reports'])/'dcc-probe.json')
        self.assertFalse(report['otherDccSearchComplete']); self.assertIn('local installation records',report['nextAction'])
        self.assertIn('before asking the user',report['nextAction'])

    def test_no_builtin_candidates_does_not_claim_no_local_dcc(self):
        cfg=dict(reports=str(self.root/'no-candidates'))
        with patch.object(w,'discover',return_value=dict(candidates={k:[] for k in w.DCC_PRIORITY})),patch.object(w,'run') as execute:
            with self.assertRaisesRegex(RuntimeError,'other DCCs have not been exhausted'): w.verify_dcc(cfg)
        execute.assert_not_called(); report=w.read(Path(cfg['reports'])/'dcc-probe.json')
        self.assertFalse(report['otherDccSearchComplete']); self.assertEqual([],report['attempts'])


if __name__=='__main__': unittest.main()
