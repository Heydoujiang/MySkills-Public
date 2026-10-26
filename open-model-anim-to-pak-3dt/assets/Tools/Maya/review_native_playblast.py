"""Read native AVI frames and create an independent, same-view review sheet."""
import argparse
import hashlib
import json
import struct
from pathlib import Path
from PIL import Image, ImageDraw

parser=argparse.ArgumentParser()
parser.add_argument('video')
parser.add_argument('--output',required=True)
args=parser.parse_args()
video=Path(args.video).resolve()
out=Path(args.output).resolve()
out.mkdir(parents=True,exist_ok=True)
frame_chunks=[]
headers={}
with video.open('rb') as stream:
    head=stream.read(12)
    if head[:4]!=b'RIFF' or head[8:12]!=b'AVI ':
        raise ValueError('Not a native AVI')
    declared=struct.unpack_from('<I',head,4)[0]+8
    def walk(start,end):
        pos=start
        while pos+8<=end:
            stream.seek(pos)
            tag,size=struct.unpack('<4sI',stream.read(8))
            data=pos+8
            if data+size>end:
                raise ValueError('Invalid AVI chunk size')
            if tag in (b'LIST',b'RIFF'):
                stream.read(4)
                walk(data+4,data+size)
            elif tag==b'avih':
                headers['avih']=struct.unpack('<14I',stream.read(56))
            elif tag==b'strf' and size>=40:
                headers['bitmap']=struct.unpack('<IiiHHIIiiII',stream.read(40))
            elif tag in (b'00db',b'00dc'):
                frame_chunks.append((data,size))
            pos=data+size+size%2
    walk(12,declared)
    avih=headers['avih'];bitmap=headers['bitmap']
    width,height,bits,compression=bitmap[1],bitmap[2],bitmap[4],bitmap[5]
    if bits not in (24,32) or compression!=0:
        raise ValueError('This reader expects uncompressed RGB AVI')
    if len(frame_chunks)!=avih[4]:
        raise ValueError('Actual frame count does not match the AVI header')
    count=len(frame_chunks)
    fps=1e6/avih[0]
    chosen=sorted(set(round((count-1)*p) for p in (0,.18,.30,.43,.55,.68,.82,1)))
    tile_w,tile_h=360,270
    sheet=Image.new('RGB',(tile_w*4,(tile_h+24)*2),(18,23,30))
    draw=ImageDraw.Draw(sheet)
    stride=((width*bits+31)//32)*4
    hashes=[]
    for index,(offset,size) in enumerate(frame_chunks):
        stream.seek(offset)
        raw=stream.read(size)
        hashes.append(hashlib.sha256(raw).hexdigest())
        if index in chosen:
            im=Image.frombytes('RGB',(width,abs(height)),raw,'raw','BGR' if bits==24 else 'BGRX',stride,-1 if height>0 else 1)
            if index in (0,count-1):
                im.save(out/('%s_%s.png'%(video.stem,'assembled' if index==0 else 'exploded')))
            cell=chosen.index(index);x=(cell%4)*tile_w;y=(cell//4)*(tile_h+24)
            im.thumbnail((tile_w,tile_h))
            sheet.paste(im,(x,y))
            draw.text((x+8,y+tile_h+5),'%.2fs | frame %d'%(index/fps,index+1),fill=(230,235,240))
    sheet_path=out/(video.stem+'_review.png')
    sheet.save(sheet_path)
changed=[i for i in range(1,count) if hashes[i]!=hashes[i-1]]
internal_runs=[]
if changed:
    run_start=0
    for i in range(1,count+1):
        if i==count or hashes[i]!=hashes[run_start]:
            # Exclude intentional opening/closing holds; report visible pauses,
            # without assuming identical pixels prove identical geometry.
            if i-run_start>=3 and run_start>=changed[0] and i-1<=changed[-1]:
                internal_runs.append({'startFrame':run_start+1,'endFrame':i,
                                      'unchangedSeconds':(i-run_start-1)/fps})
            run_start=i
report={'video':str(video),'reviewSheet':str(sheet_path),'width':width,'height':abs(height),
        'frameCount':count,'fps':fps,'durationSeconds':count/fps,'distinctFrames':len(set(hashes)),
        'firstChangingFrame':changed[0]+1 if changed else None,
        'lastChangingFrame':changed[-1]+1 if changed else None,
        'internalUnchangedFrameRuns':internal_runs,
        'assembledAndExplodedDiffer':hashes[0]!=hashes[-1],'fileBytes':video.stat().st_size,
        'declaredRiffBytes':declared,'trailingPaddingBytes':video.stat().st_size-declared}
if not report['assembledAndExplodedDiffer'] or report['distinctFrames']<5:
    raise ValueError('No usable animated change in the output')
(out/(video.stem+'_video_check.json')).write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps(report,indent=2))
