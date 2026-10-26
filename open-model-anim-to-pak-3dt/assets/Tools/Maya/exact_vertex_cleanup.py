"""Merge coincident vertices on an export-only copy and verify every face corner."""
import json,time
from pathlib import Path
import numpy as np
import maya.cmds as cmds
import maya.api.OpenMaya as om


def function(node):
    shapes=cmds.listRelatives(node,shapes=True,fullPath=True,noIntermediate=True,type='mesh') or []
    assert len(shapes)==1,node
    sel=om.MSelectionList();sel.add(shapes[0]);return om.MFnMesh(sel.getDagPath(0))


def capture(fn):
    counts,indices=fn.getVertices();counts=np.asarray(counts,dtype=np.int32);indices=np.asarray(indices,dtype=np.int32)
    points=np.asarray(fn.getPoints(om.MSpace.kObject),dtype=np.float64)
    corners=points[indices,:3]
    normals=np.asarray(fn.getFaceVertexNormals(om.MSpace.kObject),dtype=np.float32)
    uvs={}
    for name in fn.getUVSetNames():
        c,ids=fn.getAssignedUVs(name);u,v=fn.getUVs(name)
        coords=np.column_stack((np.asarray(u,dtype=np.float32),np.asarray(v,dtype=np.float32)))
        uvs[name]=(np.asarray(c,dtype=np.int32),coords[np.asarray(ids,dtype=np.int32)])
    shaders,assignments=fn.getConnectedShaders(0)
    return {'counts':counts,'indices':indices,'positions':corners,'normals':normals,'uvs':uvs,
            'shaders':[om.MFnDependencyNode(x).name() for x in shaders],
            'materials':np.asarray(assignments,dtype=np.int32)}


def clean(inspection,output):
    cmds.undoInfo(state=False)
    rows=[]
    for part in sorted(inspection['parts'],key=lambda r:r['vertices']):
        started=time.monotonic();node=part['sourcePath'];fn=function(node)
        assert not fn.getColorSetNames(),'Color-set cleanup requires an explicit adapter'
        vertices=fn.numVertices;faces=fn.numPolygons;original=capture(fn)
        # Keep coincident corners of degenerate polygons separate: welding those
        # corners would cause Maya to remove the polygon. All input faces stay.
        starts=np.cumsum(original['counts'])-original['counts'];protected=[]
        for count in np.unique(original['counts']):
            ids=np.flatnonzero(original['counts']==count)
            corners=starts[ids,None]+np.arange(count)[None,:]
            p=original['positions'][corners];repeated=np.zeros(len(ids),dtype=bool)
            for i in range(count):
                for j in range(i):repeated|=np.all(p[:,i]==p[:,j],axis=1)
            if repeated.any():protected.extend(original['indices'][corners[repeated]].ravel().tolist())
        protected=np.unique(protected).astype(np.int64);selection=[];previous=0
        for vertex in protected:
            if previous<vertex:selection.append(node+'.vtx[%d:%d]'%(previous,vertex-1))
            previous=int(vertex)+1
        if previous<vertices:selection.append(node+'.vtx[%d:%d]'%(previous,vertices-1))
        cmds.polyNormalPerVertex(node,freezeNormal=True)
        # An empty explicit list would fall back to Maya's current selection.
        # If every vertex is protected, leave topology intact instead.
        if not len(protected) or selection:
            cmds.polyMergeVertex(selection if len(protected) else node,distance=0.0,alwaysMergeTwoVertices=False,constructionHistory=False,texture=False)
        cmds.delete(node,constructionHistory=True)
        fn=function(node);result=capture(fn)
        assert fn.numPolygons==faces and np.array_equal(original['counts'],result['counts']),node+' faces changed'
        # Maya's merge can round the average of coincident float positions.
        # Restore exact original coordinates through the unchanged face-corner mapping.
        restored=np.asarray(fn.getPoints(om.MSpace.kObject),dtype=np.float64)
        restored[result['indices'],:3]=original['positions']
        fn.setPoints(om.MPointArray(restored.tolist()),om.MSpace.kObject)
        del restored,result
        result=capture(fn)
        pos_error=float(np.max(np.abs(original['positions']-result['positions'])))
        normal_error=float(np.max(np.abs(original['normals']-result['normals'])))
        assert pos_error<1e-6,(node,'position error',pos_error)
        assert normal_error<1e-6,(node,'normal error',normal_error)
        assert original['shaders']==result['shaders'] and np.array_equal(original['materials'],result['materials']),node+' materials changed'
        assert set(original['uvs'])==set(result['uvs']),node+' UV sets changed'
        uv_error=0.
        for name,(counts,coords) in original['uvs'].items():
            other_counts,other_coords=result['uvs'][name]
            assert np.array_equal(counts,other_counts),node+' UV assignments changed'
            if coords.size:uv_error=max(uv_error,float(np.max(np.abs(coords-other_coords))))
        assert uv_error<1e-7,(node,'UV error',uv_error)
        row={'part':part['sourceName'],'verticesBefore':vertices,'verticesAfter':fn.numVertices,'faces':faces,
             'maxFaceCornerPositionErrorCm':pos_error,'maxFaceCornerNormalError':normal_error,
             'maxFaceCornerUVError':uv_error,'materialAssignmentsUnchanged':True,'seconds':round(time.monotonic()-started,2)}
        row['protectedDegenerateFaceVertices']=len(protected)
        rows.append(row);print('EXACT_WELD_VERIFIED',json.dumps(row),flush=True)
        del original,result,fn
    report={'exportCopyOnly':True,'approvedSourceSceneSaved':False,'facesPreserved':True,'faceCornerNormalsUVMaterialsVerified':True,
            'verticesBefore':sum(r['verticesBefore'] for r in rows),'verticesAfter':sum(r['verticesAfter'] for r in rows),'parts':rows}
    Path(output).write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report
