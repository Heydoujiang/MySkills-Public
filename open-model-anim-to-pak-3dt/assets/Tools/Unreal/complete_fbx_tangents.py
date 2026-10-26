"""Add missing tangent frames to UV-less FBX meshes, preserving all existing node properties byte for byte.

No UVs, normals, geometry, materials, skinning, or animation are modified.
The existing FBX footer remains aligned by padding the name of a new layer.
"""
from pathlib import Path
import argparse,hashlib,json,struct,zlib
import numpy as np

HEADER=struct.Struct('<QQQB')
ZERO=b'\0'*HEADER.size

class Node:
    def __init__(self,name,props=b'',count=0,children=None):
        self.name=name.encode() if isinstance(name,str) else name
        self.raw=props;self.count=count;self.children=children or [];self.values=[]
        self.offset=0;self.length=len(props);self.terminal=bool(self.children)
    def size(self):return HEADER.size+len(self.name)+self.length+sum(n.size() for n in self.children)+(HEADER.size if self.terminal else 0)
    def child(self,name):return next(c for c in self.children if c.name==name.encode())

def value(f):
    kind=f.read(1)
    sizes={b'Y':'h',b'C':'?',b'I':'i',b'F':'f',b'D':'d',b'L':'q'}
    if kind in sizes:
        fmt=struct.Struct('<'+sizes[kind]);return fmt.unpack(f.read(fmt.size))[0]
    if kind in (b'S',b'R'):
        count=struct.unpack('<I',f.read(4))[0]
        if kind==b'R':f.seek(count,1);return None
        return f.read(count).decode('utf-8',errors='replace')
    assert kind in (b'f',b'd',b'l',b'i',b'b',b'c'),kind
    count,encoding,length=struct.unpack('<III',f.read(12));offset=f.tell();f.seek(length,1)
    return {'kind':kind,'count':count,'encoding':encoding,'offset':offset,'length':length}

def parse(f):
    end,count,length,nlen=HEADER.unpack(f.read(HEADER.size))
    if not end:return None
    n=Node(f.read(nlen));n.offset=f.tell();n.length=length;n.count=count;n.raw=None
    n.values=[value(f) for _ in range(count)]
    assert f.tell()==n.offset+length
    while f.tell()<end:
        c=parse(f)
        if c is None:n.terminal=True;break
        n.children.append(c)
    assert f.tell()==end,(n.name,f.tell(),end)
    return n

def load(path):
    with path.open('rb') as f:
        prefix=f.read(27);assert prefix[:23]==b'Kaydara FBX Binary  \x00\x1a\x00'
        assert struct.unpack('<I',prefix[23:])[0]>=7500
        roots=[]
        while True:
            n=parse(f)
            if n is None:break
            roots.append(n)
        footer=f.read()
    return prefix,roots,footer

def scalar(name,value):
    if isinstance(value,str):
        data=value.encode();return Node(name,b'S'+struct.pack('<I',len(data))+data,1)
    return Node(name,b'I'+struct.pack('<i',value),1)

def array(name,data):
    a=np.asarray(data,dtype='<f8').ravel();packed=zlib.compress(a.tobytes(),3)
    return Node(name,b'd'+struct.pack('<III',len(a),1,len(packed))+packed,1)

def read_array(f,n):
    p=n.values[0];assert p['kind'] in (b'd',b'f',b'i')
    f.seek(p['offset']);data=f.read(p['length'])
    if p['encoding']:data=zlib.decompress(data)
    result=np.frombuffer(data,dtype={b'd':'<f8',b'f':'<f4',b'i':'<i4'}[p['kind']])
    assert len(result)==p['count'];return result

def basis_layer(name,vectors,w):
    n=Node('LayerElement'+name,b'I'+struct.pack('<i',0),1,[scalar('Version',102),scalar('Name','OpenModelBasis'),
           scalar('MappingInformationType','ByPolygonVertex'),scalar('ReferenceInformationType','Direct'),
           array(name+'s',vectors),array(name+'sW',w)])
    return n

def write_node(src,dst,n):
    end=dst.tell()+n.size()
    dst.write(HEADER.pack(end,n.count,n.length,len(n.name)));dst.write(n.name)
    if n.raw is not None:dst.write(n.raw)
    else:
        src.seek(n.offset);remaining=n.length
        while remaining:
            data=src.read(min(8*1024*1024,remaining));assert data;dst.write(data);remaining-=len(data)
    for c in n.children:write_node(src,dst,c)
    if n.terminal:dst.write(ZERO)
    assert dst.tell()==end

def digest_properties(f,n):
    f.seek(n.offset);h=hashlib.sha256();remaining=n.length
    while remaining:
        data=f.read(min(8*1024*1024,remaining));assert data;h.update(data);remaining-=len(data)
    return h.hexdigest()

def verify_original_properties(src,dst,old,new):
    assert old.name==new.name and old.count==new.count and old.length==new.length
    assert digest_properties(src,old)==digest_properties(dst,new),old.name
    assert len(new.children)>=len(old.children)
    count=1
    for a,b in zip(old.children,new.children):count+=verify_original_properties(src,dst,a,b)
    return count

def main(source,destination,report):
    assert not destination.exists()
    prefix,roots,footer=load(source)
    original_size=sum(n.size() for n in roots);changes=[];first_name=None
    objects=next(n for n in roots if n.name==b'Objects')
    with source.open('rb') as src:
        for mesh in objects.children:
            if mesh.name!=b'Geometry' or mesh.values[-1]!='Mesh':continue
            types={n.name for n in mesh.children}
            if b'LayerElementTangent' in types and b'LayerElementBinormal' in types:continue
            assert b'LayerElementTangent' not in types and b'LayerElementBinormal' not in types
            assert b'LayerElementUV' not in types,'Only UV-less mesh tangent completion is permitted'
            normals=mesh.child('LayerElementNormal')
            assert normals.child('MappingInformationType').values==['ByPolygonVertex']
            assert normals.child('ReferenceInformationType').values==['Direct']
            normal=read_array(src,normals.child('Normals')).reshape((-1,3))
            corners=mesh.child('PolygonVertexIndex').values[0]['count'];assert corners==len(normal)
            length=np.linalg.norm(normal,axis=1);good=length>1e-12
            unit=np.zeros_like(normal);unit[good]=normal[good]/length[good,None]
            unit[~good]=[0,0,1]
            axis=np.zeros_like(unit);axis[np.arange(len(unit)),np.argmin(np.abs(unit),axis=1)]=1
            tangent=np.cross(unit,axis);tangent/=np.linalg.norm(tangent,axis=1)[:,None]
            binormal=np.cross(unit,tangent)
            # Copy the FBX direction-vector W convention from its normal layer.
            w=read_array(src,normals.child('NormalsW'))
            t=basis_layer('Tangent',tangent,w);b=basis_layer('Binormal',binormal,w)
            mesh.children.extend([t,b]);mesh.terminal=True
            if first_name is None:first_name=t.child('Name')
            layer=mesh.child('Layer')
            for kind in ('Tangent','Binormal'):
                layer.children.append(Node('LayerElement',children=[scalar('Type','LayerElement'+kind),scalar('TypedIndex',0)]))
            layer.terminal=True
            changes.append({'geometryId':mesh.values[0],'corners':corners,'zeroSourceNormals':int((~good).sum()),'UVsAdded':False})
            print('TANGENT_COMPLETED',json.dumps(changes[-1]),flush=True)
            del normal,unit,axis,tangent,binormal,length,good,w
    assert changes,'No missing tangent layers'
    delta=sum(n.size() for n in roots)-original_size;pad=(-delta)%16
    padded=scalar('Name','OpenModelBasis'+'_'*pad)
    first_name.raw=padded.raw;first_name.length=padded.length
    assert (sum(n.size() for n in roots)-original_size)%16==0
    with source.open('rb') as src,destination.open('wb') as dst:
        dst.write(prefix)
        for n in roots:write_node(src,dst,n)
        dst.write(ZERO);dst.write(footer)
    # Reparse both files; compare every pre-existing node's complete properties.
    p0,r0,f0=load(source);p1,r1,f1=load(destination)
    assert p0==p1 and f0==f1 and len(r0)==len(r1)
    with source.open('rb') as src,destination.open('rb') as dst:
        verified=sum(verify_original_properties(src,dst,a,b) for a,b in zip(r0,r1))
    result={'passed':True,'source':str(source.resolve()),'file':str(destination.resolve()),
            'originalNodePropertyBlocksVerified':verified,'geometryNormalsUVMaterialsSkinAnimationPropertiesUnchanged':True,
            'tangentOnlyAdditions':changes,'bytes':destination.stat().st_size,
            'reason':'UV-less meshes have no texture tangent frame; add a valid orthogonal frame from unchanged normals to avoid UE corner-by-corner fallback calculations.'}
    report.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print('FBX_TANGENT_COMPLETION_VERIFIED',json.dumps(result),flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path,help='Binary FBX 7500+')
    parser.add_argument('destination',type=Path,help='New FBX output; never overwrites the source')
    parser.add_argument('report',type=Path,help='JSON verification report')
    args=parser.parse_args()
    main(args.source,args.destination,args.report)
