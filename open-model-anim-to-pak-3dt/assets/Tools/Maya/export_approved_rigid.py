"""Export a confirmed translation-only transform animation without saving over it.

Adapter contract: config.motion contains sourcePath rows, config.endFrame defines
source frames 1..endFrame; inspection.parts has stable partId/sourcePath/centerCm.
This adapter retains the tested one-frame endpoint hold when exporting 0..endFrame.
Rotation/scale animation is deliberately rejected; adapt full matrices separately.
For explode_pipeline-created rigs use its export command instead. This helper does
not itself grant animation approval; the invoking workflow must verify that first.
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path
import maya.standalone
maya.standalone.initialize(name='python')
import maya.cmds as cmds
import maya.mel as mel
import maya.api.OpenMaya as om
import maya.api.OpenMayaAnim as oma


def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(8*1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def matrix(node):
    return om.MMatrix(cmds.xform(node,q=True,ws=True,matrix=True))


def matrix_data(node):
    m=matrix(node);tm=om.MTransformationMatrix(m);q=tm.rotation(asQuaternion=True)
    return {'matrixRowMajor':list(m),'translationCm':list(tm.translation(om.MSpace.kWorld)),
            'rotationXyzw':[q.x,q.y,q.z,q.w],'scale':list(tm.scale(om.MSpace.kWorld))}


def main():
    p=argparse.ArgumentParser();p.add_argument('model');p.add_argument('scene',type=Path);p.add_argument('config',type=Path);p.add_argument('inspection',type=Path);p.add_argument('output',type=Path);p.add_argument('--no-smoothing-groups',action='store_true');p.add_argument('--merge-exact-vertices',action='store_true')
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
    config=json.loads(a.config.read_text(encoding='utf-8-sig'));inspection=json.loads(a.inspection.read_text(encoding='utf-8-sig'))
    if (a.output/('SK_'+a.model+'.fbx')).exists():raise FileExistsError('Back up the existing FBX before export')
    if 'motion' not in config or 'endFrame' not in config:raise ValueError('Expected transform-motion adapter config; pipeline rigs use explode_pipeline export')
    before=digest(a.scene)
    print('OPEN_APPROVED_SCENE',str(a.scene),flush=True)
    cmds.file(str(a.scene),open=True,force=True,executeScriptNodes=False,ignoreVersion=True)
    cmds.currentUnit(linear='cm',time='ntsc');cmds.evaluationManager(mode='off');cmds.currentTime(1,edit=True)
    if a.merge_exact_vertices:
        from exact_vertex_cleanup import clean
        clean(inspection,a.output/'exact_vertex_cleanup.json')
    records=[];motion={r['sourcePath']:r for r in config['motion']}
    end=int(config['endFrame'])
    for i,part in enumerate(inspection['parts']):
        node=part['sourcePath'];assert cmds.objExists(node),node
        base=cmds.getAttr(node+'.worldMatrix[0]',time=1)
        values=[]
        for f in range(end+1):
            m=cmds.getAttr(node+'.worldMatrix[0]',time=min(f+1,end))
            if max(abs(m[k]-base[k]) for k in range(12))>1e-6:raise RuntimeError('Non-translation source motion: '+node)
            values.append([m[k]-base[k] for k in (12,13,14)])
        shapes=cmds.listRelatives(node,shapes=True,fullPath=True,noIntermediate=True,type='mesh') or []
        assert shapes,node
        rec={'partId':part['partId'],'sourcePath':node,'boneName':'part_%03d'%(i+1),'baseMatrix':base,
             'pivot':part.get('centerCm',[(part['boundsCm'][k]+part['boundsCm'][k+3])/2 for k in range(3)]) if 'boundsCm' in part else part['centerCm'],
             'offsets':values,'shapes':shapes,'samples':[]}
        for shape in shapes:
            sel=om.MSelectionList();sel.add(shape);dag=sel.getDagPath(0);fn=om.MFnMesh(dag)
            ids=sorted(set(round(k*(fn.numVertices-1)/5) for k in range(6)))
            rec['samples'].append((dag,[(idx,fn.getPoint(idx,om.MSpace.kWorld)) for idx in ids]))
        records.append(rec)
    print('SOURCE_TRACKS_CAPTURED',len(records),flush=True)
    for rec in records:
        cmds.cutKey(rec['sourcePath'],attribute=('tx','ty','tz'),clear=True)
        cmds.xform(rec['sourcePath'],ws=True,matrix=rec['baseMatrix'])
    cmds.currentTime(0,edit=True);cmds.select(clear=True);root=cmds.joint(name='root',position=(0,0,0))
    for rec in records:
        cmds.select(root,replace=True);bone=cmds.joint(name=rec['boneName'],position=rec['pivot']);rec['boneName']=bone
        skin=cmds.skinCluster(bone,rec['sourcePath'],toSelectedBones=True,maximumInfluences=1,
                              obeyMaxInfluences=True,normalizeWeights=1,name='rigid_'+rec['partId'])[0]
        skin_sel=om.MSelectionList();skin_sel.add(skin);skin_fn=oma.MFnSkinCluster(skin_sel.getDependNode(0))
        assert len(skin_fn.influenceObjects())==1
        for dag,samples in rec['samples']:
            component=om.MFnSingleIndexedComponent();comp=component.create(om.MFn.kMeshVertComponent)
            component.addElements([idx for idx,_ in samples]);weights,count=skin_fn.getWeights(dag,comp)
            assert count==1 and all(abs(w-1)<1e-8 for w in weights)
        for f,delta in enumerate(rec['offsets']):
            for axis,pivot,value in zip(('tx','ty','tz'),rec['pivot'],delta):
                cmds.setKeyframe(bone,attribute=axis,time=f,value=pivot+value,inTangentType='linear',outTangentType='linear')
        print('BOUND',rec['sourcePath'],flush=True)
    for f in (0,end):
        for axis in ('tx','ty','tz','rx','ry','rz'):cmds.setKeyframe(root,attribute=axis,time=f,value=0,inTangentType='linear',outTangentType='linear')
    cmds.playbackOptions(minTime=0,maxTime=end,animationStartTime=0,animationEndTime=end)
    samples=[];max_error=0.
    for progress in sorted(set([i/10 for i in range(11)]+[.037,.283,.619,.947])):
        frame=progress*end;cmds.currentTime(frame,edit=True,update=True)
        bones={'root':matrix_data(root)}
        for rec in records:bones[rec['boneName']]=matrix_data(rec['boneName'])
        if progress in (0,.5,1):
            for rec in records:
                actual_delta=[bones[rec['boneName']]['translationCm'][k]-rec['pivot'][k] for k in range(3)]
                for dag,points in rec['samples']:
                    fn=om.MFnMesh(dag)
                    for idx,rest in points:
                        expected=rest+om.MVector(*actual_delta)
                        max_error=max(max_error,(fn.getPoint(idx,om.MSpace.kWorld)-expected).length())
        samples.append({'progress':progress,'frame':frame,'bones':bones})
    assert max_error<.002,max_error
    validation={'schemaVersion':1,'assetName':a.model,'fps':30,'durationFrames':end,'durationSeconds':end/30,
                'coordinateConvention':{'upAxis':'y','linearUnit':'cm','matrix':'row-major, row-vector'},
                'partCount':len(records),'boneCount':len(records)+1,'passed':True,'maxRigidVertexErrorCm':max_error,
                'parts':[{'partId':r['partId'],'boneName':r['boneName'],'meshName':r['sourcePath']} for r in records],
                'samples':samples,'approvedScene':str(a.scene),'approvedSceneSha256':before}
    (a.output/'validation_samples.json').write_text(json.dumps(validation,indent=2),encoding='utf-8')
    cmds.currentTime(0,edit=True);cmds.loadPlugin('fbxmaya',quiet=True);mel.eval('FBXResetExport;')
    options=['FBXExportFileVersion -v FBX201800','FBXExportSmoothingGroups -v '+('false' if a.no_smoothing_groups else 'true'),'FBXExportTangents -v true',
             'FBXExportSmoothMesh -v false','FBXExportSkins -v true','FBXExportShapes -v false','FBXExportConstraints -v false',
             'FBXExportCameras -v false','FBXExportLights -v false','FBXExportInputConnections -v false','FBXExportEmbeddedTextures -v false',
             'FBXExportBakeComplexAnimation -v true','FBXExportBakeComplexStart -v 0','FBXExportBakeComplexEnd -v %d'%end,
             'FBXExportBakeComplexStep -v 1','FBXExportBakeResampleAnimation -v true','FBXExportApplyConstantKeyReducer -v false']
    for option in options:mel.eval(option+';')
    cmds.select([root]+[r['sourcePath'] for r in records],replace=True)
    output=a.output/('SK_'+a.model+'.fbx')
    print('EXPORT_FBX',str(output),flush=True);mel.eval('FBXExport -f "%s" -s;'%str(output).replace('\\','/'))
    assert digest(a.scene)==before,'Approved Maya file was modified'
    result={'fbx':str(output),'fbxSha256':digest(output),'mayaFileSha256':before,'parts':len(records),'bones':len(records)+1,
            'durationSeconds':end/30,'maxRigidVertexErrorCm':max_error,'sourceMayaUnchanged':True}
    (a.output/'export_manifest.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print('RIGID_EXPORT_OK',json.dumps(result),flush=True)

try:main()
finally:maya.standalone.uninitialize()
