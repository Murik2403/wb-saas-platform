"""Build one reviewed public course directory; never include private proofs/databases."""
from __future__ import annotations
import argparse
import hashlib
import json
import shutil
from pathlib import Path

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--course-manifest',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--overview',type=Path)
    parser.add_argument('--reviewed-materials-approved',action='store_true',required=True)
    args=parser.parse_args()
    catalog=json.loads((Path(__file__).resolve().parents[1]/'gateway/training_catalog.json').read_text(encoding='utf-8'))
    report=json.loads(args.course_manifest.read_text(encoding='utf-8'))
    lessons={row['lesson']:row for row in report['basic_section_lessons']}
    copies=[]; captions={}
    for item in catalog:
        row=lessons[item['slug']]
        assert row['status']=='ready_verified','Unfinished or unreviewed media: '+item['slug']
        assert row.get('visual_review_verified') is True,'Visual acceptance missing'
        source=Path(row['video']).resolve()
        assert source.is_relative_to(args.course_manifest.resolve().parent),'Outside course directory'
        proof=row['proof'];body=source.read_bytes();digest=hashlib.sha256(body).hexdigest()
        assert proof.get('full_decode_verified') is True,'Full decode acceptance missing'
        assert digest==proof['sha256'] and len(body)==proof['bytes'],'Media proof changed'
        assert float(proof['duration_seconds'])==item['duration'],'Duration mismatch'
        copies.append((item['slug'],source,digest,len(body)))
        caption_path=source.with_name('captions-ru.vtt')
        assert caption_path.is_file() and caption_path.read_text(encoding='utf-8').startswith('WEBVTT'),'Missing subtitles'
        caption_body=caption_path.read_bytes(); caption_digest=hashlib.sha256(caption_body).hexdigest()
        captions[item['slug']]=(caption_path,caption_digest,len(caption_body))
    if args.overview:
        source=args.overview.resolve();body=source.read_bytes()
        copies.append(('overview',source,hashlib.sha256(body).hexdigest(),len(body)))
    assert not args.output.exists(),'Create a fresh immutable release directory'
    args.output.mkdir(parents=True)
    videos={}
    for slug,source,digest,size in copies:
        filename=f'{slug}.{digest}.mp4'
        shutil.copyfile(source,args.output/filename)
        assert hashlib.sha256((args.output/filename).read_bytes()).hexdigest()==digest
        videos[slug]={'filename':filename,'sha256':digest,'bytes':size}
        if slug in captions:
            caption_path,caption_digest,caption_size=captions[slug]
            caption_name=f'{slug}.{caption_digest}.vtt'
            shutil.copyfile(caption_path,args.output/caption_name)
            assert hashlib.sha256((args.output/caption_name).read_bytes()).hexdigest()==caption_digest
            videos[slug]['captions']={'filename':caption_name,'sha256':caption_digest,'bytes':caption_size}
    manifest={'version':1,'approved':True,'synthetic':True,'videos':videos}
    (args.output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,sort_keys=True,indent=2)+'\n',encoding='utf-8')
    assets={path.name:{'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'bytes':path.stat().st_size} for path in args.output.iterdir()}
    pack_proof={'version':1,'approved':True,'synthetic':True,'assets':assets}
    (args.output/'MEDIA_PACK_PROOF.json').write_text(json.dumps(pack_proof,sort_keys=True,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'files':len(videos),'total_video_bytes':sum(row['bytes'] for row in videos.values()),'manifest_sha256':hashlib.sha256((args.output/'manifest.json').read_bytes()).hexdigest(),'directory':str(args.output)},ensure_ascii=False))
if __name__=='__main__':main()
