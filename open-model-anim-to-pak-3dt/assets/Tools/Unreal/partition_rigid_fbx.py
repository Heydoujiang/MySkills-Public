"""Partition rigid geometry while retaining the complete, byte-identical skeleton and animation."""
import argparse,copy,hashlib,json,re
from pathlib import Path
from complete_fbx_tangents import load,scalar,write_node,ZERO,verify_original_properties,read_array

def sha(p):
    with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

def replace_scalar(node,value):
    other=scalar(node.name.decode(),value);node.raw=other.raw;node.length=other.length;node.values=[value]

def string_property(value):
    import struct
    data=value.encode();return b'S'+struct.pack('<I',len(data))+data

def main(source,out,count,object_id,max_triangles=None):
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',object_id):
        raise ValueError('object_id must be an ASCII asset identifier')
    if count < 1:raise ValueError('group count must be positive')
    if out.exists():raise FileExistsError('Use a new output directory; no existing files are replaced')
    prefix,roots,footer=load(source)
    objects=next(n for n in roots if n.name==b'Objects')
    connections=next(n for n in roots if n.name==b'Connections')
    by_id={n.values[0]:n for n in objects.children}
    geometries=[n for n in objects.children if n.name==b'Geometry' and n.values[-1]=='Mesh']
    mesh_ids={n.values[0] for n in geometries}
    if count > len(geometries):raise ValueError('group count exceeds available mesh objects')
    triangles={}
    with source.open('rb') as src:
        for n in geometries:
            corners=read_array(src,n.child('PolygonVertexIndex'))
            if len(corners)%3 or not (corners[2::3]<0).all() or not (corners[0::3]>=0).all() or not (corners[1::3]>=0).all():
                raise ValueError('Only pre-triangulated FBX geometry is supported; triangulate an export copy in the DCC first')
            triangles[n.values[0]]=len(corners)//3
    groups=[[] for _ in range(count)];sizes=[0]*count
    for n in sorted(geometries,key=lambda n:triangles[n.values[0]],reverse=True):
        index=min(range(count),key=lambda i:sizes[i]);groups[index].append(n.values[0]);sizes[index]+=triangles[n.values[0]]
    assert all(groups)
    if max_triangles is not None and max(sizes)>max_triangles:
        raise ValueError('A group exceeds --max-triangles; increase group count or split the oversized source mesh in an export copy')
    out.mkdir(parents=True,exist_ok=False)
    source_size=sum(n.size() for n in roots)
    results=[];all_geometries=[]
    for index,group in enumerate(groups):
        selected=set(group);removed=mesh_ids-selected
        # OO links: Skin -> Geometry, Cluster -> Skin, Geometry -> Model.
        for _ in range(2):
            removed|={n.values[1] for n in connections.children
                      if len(n.values)>=3 and n.values[0]=='OO' and n.values[2] in removed
                      and n.values[1] in by_id and by_id[n.values[1]].name==b'Deformer'}
        absent_models={n.values[2] for n in connections.children
                       if len(n.values)>=3 and n.values[0]=='OO' and n.values[1] in mesh_ids-selected
                       and n.values[2] in by_id and by_id[n.values[2]].name==b'Model'}
        clone=copy.deepcopy(roots);obj=next(n for n in clone if n.name==b'Objects')
        obj.children=[n for n in obj.children if n.values[0] not in removed]
        changed_models=set()
        import struct
        for n in obj.children:
            if n.values[0] in absent_models:
                assert n.values[2]=='Mesh'
                n.raw=b'L'+struct.pack('<q',n.values[0])+string_property(n.values[1])+string_property('Null')
                n.length=len(n.raw);n.values[2]='Null';changed_models.add(n.values[0])
        links=next(n for n in clone if n.name==b'Connections')
        links.children=[n for n in links.children if n.values[1] not in removed and n.values[2] not in removed]
        definitions=next(n for n in clone if n.name==b'Definitions')
        replace_scalar(definitions.child('Count'),definitions.child('Count').values[0]-len(removed))
        for n in definitions.children:
            if n.name==b'ObjectType':
                deleted=sum(by_id[x].name.decode()==n.values[0] for x in removed)
                if deleted:replace_scalar(n.child('Count'),n.child('Count').values[0]-deleted)
        creator=next(n for n in clone if n.name==b'FBXHeaderExtension').child('Creator')
        label=creator.values[0]+' | OpenModel geometry group '+str(index+1)
        replace_scalar(creator,label)
        pad=(-(sum(n.size() for n in clone)-source_size))%16;replace_scalar(creator,label+' '*pad)
        assert (sum(n.size() for n in clone)-source_size)%16==0
        path=out/('SK_%s_Group%02d.fbx'%(object_id,index+1))
        with source.open('rb') as src,path.open('wb') as dst:
            dst.write(prefix)
            for n in clone:write_node(src,dst,n)
            dst.write(ZERO);dst.write(footer)
        _,written,_=load(path);new_objects=next(n for n in written if n.name==b'Objects')
        new_by_id={n.values[0]:n for n in new_objects.children};verified=0
        with source.open('rb') as src,path.open('rb') as dst:
            for key,n in new_by_id.items():
                original=by_id[key]
                if key in changed_models:
                    assert len(n.children)==len(original.children)
                    for a,b in zip(original.children,n.children):verified+=verify_original_properties(src,dst,a,b)
                else:verified+=verify_original_properties(src,dst,original,n)
        all_geometries+=group
        digest=sha(path)
        row={'groupId':index+1,'fbx':str(path.resolve()),'sha256':digest,'fbxSha256':digest,'geometryIds':group,'triangles':sizes[index],
             'vertices':sum(by_id[g].child('Vertices').values[0]['count']//3 for g in group),
             'assetName':'SK_'+object_id+('' if index==0 else '_Group%02d'%(index+1)),
             'componentName':'AssemblyMesh'+('' if index==0 else '%02d'%(index+1)),
             'originalPropertyBlocksVerified':verified,'allRetainedGeometrySkinSkeletonAnimationDataUnchanged':True}
        results.append(row);print('PARTITION_VERIFIED',json.dumps(row),flush=True)
    assert len(all_geometries)==len(set(all_geometries))==len(geometries)
    assert sum(sizes)==sum(triangles.values())
    report={'passed':True,'objectId':object_id,'source':str(source.resolve()),'sourceSha256':sha(source),'groups':results,
            'totalMeshes':len(geometries),'totalTriangles':sum(sizes),'maxTrianglesPerGroup':max(sizes),
            'allGeometryExactlyOnce':True,'completeSkeletonAndAnimationRetainedInEveryGroup':True,
            'changes':'Remove other geometry and its skin deformers from each copy; keep transform-only parent nodes, complete skeleton, animations and retained mesh data unchanged.'}
    (out/'partition_manifest.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print('FBX_PARTITIONING_OK',json.dumps({k:v for k,v in report.items() if k!='groups'}),flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path,help='Binary FBX 7500+ with pre-triangulated rigid parts')
    parser.add_argument('out',type=Path,help='New output directory')
    parser.add_argument('count',type=int,help='Number of balanced geometry groups')
    parser.add_argument('--object-id',required=True,help='Stable model identifier used for asset names')
    parser.add_argument('--max-triangles',type=int,help='Optional per-group triangle budget, chosen for this import')
    args=parser.parse_args()
    main(args.source,args.out,args.count,args.object_id,args.max_triangles)
