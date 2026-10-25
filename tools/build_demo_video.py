"""Monta demonstração legendada a partir de capturas reais feitas pelo FFmpeg."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ffmpeg',required=True,type=Path)
    parser.add_argument('--media',type=Path,default=ROOT/'media')
    args=parser.parse_args()
    media=args.media.resolve()
    command='irm https://raw.githubusercontent.com/TechFernandesLTDA/hashpilot/main/install.ps1 | iex'
    sequence=[
        ('intro',4,'HashPilot | Tech Fernandes','Windows NVIDIA mining dashboard | GPL-3.0\n\nPowerShell install:\n'+command+'\n\nAuthor wallet is prefilled: replace it before mining.'),
        ('onboarding',6,'Choose your public wallet','Optional miner setup and local AI. Optional 5% mining-time support can be disabled.'),
        ('overview',8,'Live NVIDIA telemetry and pool payout estimates','Real local data from one RTX 3070. Earnings shown are observed, not guaranteed.'),
        ('gpu-mining',8,'Hashrate, temperature, power and persistent charts','Isolated UI capture; the active miner and GPU settings were not changed.'),
        ('earnings',8,'Observed credits and PRL / BRL / USD / BTC equivalents','History survives restarts. USD/BTC are equivalents; pool payouts remain PRL.'),
        ('benchmark',6,'Per-device measurement and optional local AI on CPU','Generic tuning is experimental. The current interface monitors GPU 0.'),
        ('outro',4,'RTX 3070 observation: 78.40 TH/s at 159.06 W mean','15-minute observation from one device; not a universal preset or A/B comparison.\n\ngithub.com/TechFernandesLTDA/hashpilot\n\nMethod and evidence: docs/performance/rtx3070.md')]
    with tempfile.TemporaryDirectory() as directory:
        temporary=Path(directory)
        shutil.copyfile(Path(os.environ['WINDIR'])/'Fonts/segoeui.ttf',temporary/'font.ttf')
        clips=[]
        subtitles=[]
        offset=0
        def stamp(seconds):
            return f'00:{seconds//60:02d}:{seconds%60:02d},000'
        for name,duration,title,body in sequence:
            (temporary/(name+'-title.txt')).write_text(title,encoding='utf-8')
            (temporary/(name+'-body.txt')).write_text(body,encoding='utf-8')
            output=temporary/(name+'.mp4')
            ffmpeg=[str(args.ffmpeg),'-hide_banner','-loglevel','error','-y']
            if name in ('intro','outro'):
                ffmpeg+=['-f','lavfi','-i','color=c=0x0A1628:s=1920x1080:r=12']
                filters=f"drawtext=fontfile=font.ttf:textfile={name}-title.txt:expansion=none:fontcolor=0x12A98E:fontsize=56:x=100:y=220,drawtext=fontfile=font.ttf:textfile={name}-body.txt:expansion=none:fontcolor=white:fontsize=30:line_spacing=22:x=100:y=380"
            else:
                ffmpeg+=['-i',str(media/'segments'/(name+'.mp4'))]
                filters=f"scale=1920:960:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:0:color=0x0A1628,drawtext=fontfile=font.ttf:textfile={name}-title.txt:expansion=none:fontcolor=0x12A98E:fontsize=30:x=40:y=985,drawtext=fontfile=font.ttf:textfile={name}-body.txt:expansion=none:fontcolor=white:fontsize=23:x=40:y=1030"
            ffmpeg+=['-vf',filters,'-t',str(duration),'-an','-r','12','-c:v','libx264','-preset','veryfast','-crf','21','-threads','2','-pix_fmt','yuv420p',str(output)]
            subprocess.run(ffmpeg,cwd=temporary,check=True,timeout=60)
            clips.append(output)
            subtitles.append(f'{len(subtitles)+1}\n{stamp(offset)} --> {stamp(offset+duration)}\n{title}\n{body}\n')
            offset+=duration
        concatenation=temporary/'concat.txt'
        concatenation.write_text('\n'.join("file '"+path.as_posix()+"'" for path in clips),encoding='utf-8')
        final=media/'hashpilot-demo.mp4'
        subprocess.run([str(args.ffmpeg),'-hide_banner','-loglevel','error','-y','-f','concat','-safe','0','-i',str(concatenation),'-c','copy','-movflags','+faststart',str(final)],check=True,timeout=60)
        (media/'hashpilot-demo.en.srt').write_text('\n'.join(subtitles),encoding='utf-8')
    print(json.dumps({'file':str(final),'seconds':offset,'bytes':final.stat().st_size,'scenes':len(sequence)}))


if __name__=='__main__': main()
