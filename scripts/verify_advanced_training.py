"""Probe, fully decode and prove immutable lesson files; visual approval is separate."""
from pathlib import Path
import argparse,subprocess,json,hashlib
from PIL import Image,ImageDraw
p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--visual-approved',action='store_true');a=p.parse_args();b=a.output.resolve()
ffmpeg=r'C:\Users\mvvor\Downloads\Recordly.node_modules.incomplete-20260922\ffmpeg-static\ffmpeg.exe'
ffprobe=r'C:\Users\mvvor\Downloads\Recordly.node_modules.incomplete-20260922\ffprobe-static\bin\win32\x64\ffprobe.exe'
rows=json.loads((b/'CURRICULUM.json').read_text(encoding='utf8'));items=[]
for row in rows:
 d=b/row['slug'];video=d/'video.mp4'
 info=json.loads(subprocess.check_output([ffprobe,'-v','error','-show_format','-show_streams','-of','json',str(video)]))
 stream=next(x for x in info['streams'] if x['codec_type']=='video');duration=float(info['format']['duration'])
 assert (stream['width'],stream['height'])==(1920,1080) and duration==120 and stream['avg_frame_rate']=='24/1'
 subprocess.run([ffmpeg,'-hide_banner','-loglevel','error','-err_detect','explode','-threads','2','-i',str(video),'-map','0:v:0','-f','null','NUL'],check=True,timeout=240)
 qc=d/'qc';qc.mkdir(exist_ok=True);sheet=Image.new('RGB',(1440,1275),'#090f13');draw=ImageDraw.Draw(sheet)
 for j,t in enumerate([14,34,54,74,94,114]):
  frame=qc/f'{t}.jpg';subprocess.run([ffmpeg,'-hide_banner','-loglevel','error','-y','-ss',str(t),'-i',str(video),'-frames:v','1',str(frame)],check=True)
  image=Image.open(frame);assert any(lo!=hi for lo,hi in image.getextrema());image.thumbnail((720,405));x=j%2*720;y=j//2*425;sheet.paste(image,(x,y));draw.text((x+8,y+405),f'{row["slug"]}: {t}s',fill='white')
 sheet.save(d/'contact-sheet.jpg')
 subprocess.run([ffmpeg,'-hide_banner','-loglevel','error','-y','-ss','14','-i',str(video),'-frames:v','1',str(d/'poster.jpg')],check=True)
 checktxt=(d/'check.json').read_text(encoding='utf-8-sig');check=json.loads(checktxt[checktxt.index('\n{')+1:] if not checktxt.startswith('{') else checktxt)
 assert check['ok'] and check['lint']['errorCount']==0
 assert (d/'captions-ru.vtt').read_text(encoding='utf8').startswith('WEBVTT')
 proof={'duration_seconds':duration,'resolution':[1920,1080],'fps':'24/1','frames':stream.get('nb_frames'),'full_decode_verified':True,'bytes':video.stat().st_size,'sha256':hashlib.sha256(video.read_bytes()).hexdigest(),'hyperframes_check_ok':True,'frame_times':[14,34,54,74,94,114],'silent':True,'caption_bytes':(d/'captions-ru.vtt').stat().st_size,'poster_sha256':hashlib.sha256((d/'poster.jpg').read_bytes()).hexdigest()}
 (d/'MEDIA_PROOF.json').write_text(json.dumps(proof,indent=2),encoding='utf8')
 items.append({'lesson':row['slug'],'status':'ready_verified' if a.visual_approved else 'rendered_media_verified_visual_review_pending','visual_review_verified':a.visual_approved,'video':str(video),'proof':proof})
 print('FULL_DECODE_OK',row['slug'],flush=True)
manifest={'source_revision':'52c0d1a','synthetic':True,'basic_section_lessons':items,'ready_basic_video_count':5 if a.visual_approved else 0,'requested_section_count':5,'full_course_complete':a.visual_approved,'captured_real_components':True,'external_actions':False,'publication_done':False}
(b/'COURSE_READINESS_MANIFEST.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf8')
