"""Optional quick MP4 copy for oversized native AVI previews; no image sequence.

Already small/native compressed video is delivered directly. auto tries NVENC
once, then libx264 on failure. Source AVI is never overwritten or removed.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import time
import uuid
from dcc_workspace import sha256
from native_playblast import avi_header


def run(args):
    result = subprocess.run(args, capture_output=True, text=True, encoding='utf-8', errors='replace',
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise RuntimeError(result.stderr[-6000:])
    return result


def encode(ffmpeg, source, candidate, quality, encoder):
    conversion = 'scale=in_range=full:out_range=limited:out_color_matrix=bt709,format=yuv420p'
    codec = (['-c:v', 'h264_nvenc', '-preset', 'p4', '-tune', 'hq', '-rc', 'vbr', '-cq', str(quality), '-b:v', '0']
             if encoder == 'nvenc' else ['-c:v', 'libx264', '-preset', 'veryfast', '-crf', str(quality)])
    run([ffmpeg, '-hide_banner', '-nostdin', '-n', '-i', str(source), '-map', '0:v:0', '-an',
         '-vf', conversion] + codec + ['-profile:v', 'high', '-colorspace', 'bt709', '-color_primaries', 'bt709',
         '-color_trc', 'bt709', '-color_range', 'tv', '-movflags', '+faststart', str(candidate)])


def compress(source, ffmpeg, quality=18, encoder='auto', min_size_mb=50., verify_existing=False, verify_quality=False):
    source = Path(source).resolve(strict=True)
    if source.suffix.lower() != '.avi' or source.stat().st_size <= min_size_mb * 1024 * 1024:
        return {'source': str(source), 'delivery': str(source), 'conversionSkipped': True,
                'reason': 'Use usable native compressed/small output directly'}
    info = avi_header(source)
    before = sha256(source)
    output = source.with_suffix('.mp4')
    if output.exists() and not verify_existing:
        raise FileExistsError('Keep the existing MP4 or validate it with --verify-existing: ' + str(output))
    started = time.monotonic()
    candidate = output if output.exists() else output.with_name(output.stem + '.encoding_' + uuid.uuid4().hex[:8] + '.mp4')
    used, fallback = 'existing', None
    try:
        if candidate != output:
            for selected in (['nvenc', 'cpu'] if encoder == 'auto' else [encoder]):
                try:
                    encode(str(ffmpeg), source, candidate, quality, selected)
                    used = selected
                    break
                except RuntimeError as error:
                    if encoder != 'auto' or selected == 'cpu':
                        raise
                    fallback = str(error)[-1500:]
                    candidate.unlink(missing_ok=True)
        decoded = run([str(ffmpeg), '-hide_banner', '-nostdin', '-i', str(candidate), '-map', '0:v:0', '-an', '-f', 'framemd5', '-'])
        frames = [line for line in decoded.stdout.splitlines() if line and not line.startswith('#')]
        dims = re.search(r'#dimensions\s+0:\s*(\d+)x(\d+)', decoded.stdout)
        timebase = re.search(r'#tb\s+0:\s*(\d+)/(\d+)', decoded.stdout)
        if len(frames) != info['frameCount'] or not dims or list(map(int, dims.groups())) != info['resolution']:
            raise RuntimeError('MP4 frame count or dimensions differ from the native AVI')
        if not timebase or abs(int(timebase[2]) / int(timebase[1]) - info['fps']) > .01:
            raise RuntimeError('MP4 frame rate differs from the native AVI')
        if not re.search(r'Video: h264', decoded.stderr):
            raise RuntimeError('Expected H.264 output')
        similarity = None
        if verify_quality:
            conversion = 'scale=in_range=full:out_range=limited:out_color_matrix=bt709,format=yuv420p'
            compared = run([str(ffmpeg), '-hide_banner', '-nostdin', '-i', str(source), '-i', str(candidate),
                            '-filter_complex', '[0:v]' + conversion + '[ref];[ref][1:v]ssim', '-an', '-f', 'null', '-'])
            match = re.search(r'All:([\d.]+)', compared.stderr)
            similarity = float(match[1]) if match else None
            if similarity is None or similarity < .99:
                raise RuntimeError('Compressed picture needs review; SSIM below .99')
        if sha256(source) != before:
            raise RuntimeError('Source changed during preview compression')
        if candidate != output:
            candidate.rename(output)
        report = {'source': str(source), 'delivery': str(output), 'sourceSha256': before,
                  'deliverySha256': sha256(output), 'sourceBytes': source.stat().st_size, 'deliveryBytes': output.stat().st_size,
                  'encoder': used, 'fallbackReason': fallback, 'lossy': True, 'codec': 'H.264 / yuv420p',
                  'seconds': round(time.monotonic() - started, 3), 'sourceUnchanged': True,
                  'validation': 'Complete decode, frame count, dimensions, frame rate and codec',
                  'ssim': similarity, **info}
        output.with_name(output.stem + '_compression.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        return report
    finally:
        if candidate != output:
            candidate.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('videos', nargs='+', type=Path)
    parser.add_argument('--ffmpeg', required=True, type=Path)
    parser.add_argument('--quality', type=int, default=18)
    parser.add_argument('--encoder', choices=('auto', 'nvenc', 'cpu'), default='auto')
    parser.add_argument('--min-size-mb', type=float, default=50.)
    parser.add_argument('--verify-existing', action='store_true')
    parser.add_argument('--verify-quality', action='store_true')
    args = parser.parse_args()
    if not 0 <= args.quality <= 51 or args.min_size_mb < 0:
        parser.error('quality must be 0..51 and min-size-mb nonnegative')
    for source in args.videos:
        print(json.dumps(compress(source, args.ffmpeg.resolve(), args.quality, args.encoder, args.min_size_mb,
                                  args.verify_existing, args.verify_quality)), flush=True)


if __name__ == '__main__':
    main()
