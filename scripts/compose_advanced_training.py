"""Author five captioned 120-second HyperFrames lessons from approved local UI captures."""
from pathlib import Path
import json,html,shutil,argparse,hashlib
from PIL import Image
p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args();b=a.output.resolve()
rows=json.loads((b/'CURRICULUM.json').read_text(encoding='utf8'))
framing=json.loads((b/'CAPTURE_FRAMING.json').read_text(encoding='utf8')) if (b/'CAPTURE_FRAMING.json').exists() else {}
fonts=b.parent/'marketshelper-current-ui-walkthrough/assets/fonts'
for row in rows:
 d=b/row['slug'];shutil.copytree(fonts,d/'assets/fonts',dirs_exist_ok=True)
 scenes=[];tweens=[];vtt=['WEBVTT',''];ledger=[]
 for j,(title,bullets) in enumerate(row['steps']):
  img=d/'assets'/f'step-{j+1}.png';im=Image.open(img);im.crop((0,0,1920,680)).save(img)
  ledger.append({'asset':str(img.relative_to(d)),'synthetic':True,'source_component_capture':True,'crop':[0,0,1920,680],'sha256':hashlib.sha256(img.read_bytes()).hexdigest(),**framing.get(row['slug']+'/'+img.name,{})})
  start=j*20
  scenes.append(f'<section id="s{j}" class="clip" data-start="{start}" data-duration="20"><header><span>MARKETSHELPER · Урок {row["id"]:02d}</span><h1>{html.escape(row["title"])}</h1></header><img class="plate" src="assets/step-{j+1}.png"><footer><div class="step">{j+1:02d}</div><div class="copy"><h2>{html.escape(title)}</h2><ul>'+''.join('<li>'+html.escape(x)+'</li>' for x in bullets)+'</ul></div></footer><div class="label">Учебный пример · Вымышленные данные · Без внешних действий</div></section>')
  tweens.append(f'tl.fromTo("#s{j} .copy",{{opacity:0,y:8}},{{opacity:1,y:0,duration:.4,ease:"power2.out"}},{start+.1});')
  for k,bullet in enumerate(bullets):
   tweens.append(f'tl.fromTo("#s{j} li:nth-child({k+1})",{{opacity:0}},{{opacity:1,duration:.3}},{start+.7+k*5});')
   def time(n):return f'{n//3600:02}:{n//60%60:02}:{n%60:02}.000'
   vtt.extend([f'{time(start+k*6)} --> {time(start+(k+1)*6 if k<2 else start+20)}',bullet,''])
 css='''@font-face{font-family:Inter;src:url(assets/fonts/inter-cyrillic-400-normal.woff2);font-weight:400;unicode-range:U+0400-04FF}@font-face{font-family:Inter;src:url(assets/fonts/inter-latin-400-normal.woff2);font-weight:400}@font-face{font-family:Inter;src:url(assets/fonts/inter-cyrillic-700-normal.woff2);font-weight:700;unicode-range:U+0400-04FF}@font-face{font-family:Inter;src:url(assets/fonts/inter-latin-700-normal.woff2);font-weight:700}*{box-sizing:border-box}html,body{margin:0;width:1920px;height:1080px;background:#090f13;color:#eaf1f5;font-family:Inter,system-ui,sans-serif}#root{width:100%;height:100%;overflow:hidden;position:relative}.clip{position:absolute;inset:0;background:#090f13;overflow:hidden}header{height:120px;padding:20px 38px}header span{font-size:24px;color:#24dbb6}h1{margin:10px 0 0;font-size:38px;line-height:1.15}.plate{position:absolute;left:0;top:120px;width:1920px;height:680px}footer{position:absolute;left:38px;right:38px;top:812px;display:flex;gap:30px}.step{font-size:90px;color:#24dbb6;font-weight:700;line-height:1.15;width:140px}.copy{flex:1}h2{font-size:31px;margin:0 0 10px}ul{padding-left:25px;margin:0;font-size:25px;line-height:1.45}.label{position:absolute;left:38px;bottom:14px;color:#b1c4cf;font-size:22px}'''
 markup='<!doctype html><html lang="ru"><head><meta charset="UTF-8"><title>'+html.escape(row['title'])+'</title><script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script><style>'+css+'</style></head><body><div id="root" data-composition-id="main" data-width="1920" data-height="1080" data-duration="120">'+''.join(scenes)+'</div><script>const tl=gsap.timeline({paused:true});'+''.join(tweens)+'window.__timelines["main"]=tl;</script></body></html>'
 (d/'index.html').write_text(markup,encoding='utf8');(d/'captions-ru.vtt').write_text('\n'.join(vtt),encoding='utf8')
 (d/'BRIEF.md').write_text('---\nworkflow: general-video\nflow: automation\nstoryboard: no\nlanguage: ru\naspect: 1920x1080\nlength: 120s\nnarration: no\n---\n\n# '+row['title']+'\n\nОбучающий материал для сайта MARKETSHELPER. User explicitly requested new finished lessons and authorizes local rendering/publication in this ongoing task. Current real React components are captured in an offline synthetic fixture; no customer financial values. Deliberate silence with readable on-screen instructions and Russian captions follows existing course. UI source revision 52c0d1a.\n',encoding='utf8')
 (d/'frame.md').write_text('# Design\n\nContinue existing MARKETSHELPER tutorial series. Dark #090f13, foreground #eaf1f5, mint #24dbb6, licensed local Inter. Native screenshot crops, clear lower instruction band, 6 instructional hard-cut scenes. No generic stock media, no unverified UI, no external actions.\n',encoding='utf8')
 (d/'ASSET_PROVENANCE.json').write_text(json.dumps(ledger,ensure_ascii=False,indent=2),encoding='utf8')
 (d/'LESSON.md').write_text('# '+row['title']+'\n\n'+'\n\n'.join('## '+title+'\n\n'+'\n'.join('- '+x for x in bullets) for title,bullets in row['steps'])+'\n\n'+'\n'.join('- '+x for x in row['cautions']),encoding='utf8')
 print('COMPOSED',row['slug'])
