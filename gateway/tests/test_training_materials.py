"""Public teaching material never exposes arbitrary mounted files or client data."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from marketing_routes import create_marketing_router, PUBLIC_MARKETING_PATHS, llms_txt
from template_setup import create_templates
import training_materials as training

class TrainingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.mount = patch.object(training, "MEDIA_ROOT", self.root)
        self.mount.start()
        app = FastAPI()
        app.include_router(create_marketing_router(create_templates()))
        self.client = TestClient(app)
    def tearDown(self):
        self.client.close()
        self.mount.stop()
        self.tmp.cleanup()
    def published(self):
        body = bytes(range(128))
        digest = hashlib.sha256(body).hexdigest()
        name = f"lesson-01-today.{digest}.mp4"
        (self.root/name).write_bytes(body)
        manifest = {"version":1,"approved":True,"videos":{"lesson-01-today":{"filename":name,"sha256":digest,"bytes":len(body)}}}
        (self.root/"manifest.json").write_text(json.dumps(manifest))
        return name, manifest
    def test_missing_assets_keep_all_text_instructions_without_broken_media(self):
        response = self.client.get('/training')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text.count('Текстовая инструкция и проверка результата'), 16)
        self.assertNotIn('<source ', response.text)
        self.assertIn('учебные', response.text)
        self.assertIn('/training', PUBLIC_MARKETING_PATHS)
        self.assertIn('/training:', llms_txt('https://marketshelper.ru'))
        for slug in ['lesson-12-tax', 'lesson-13-product-analysis', 'lesson-14-ads-analytics', 'lesson-15-fbs', 'lesson-16-self-buyouts']:
            self.assertIn(f'id="{slug}"', response.text)
        self.assertIn('не декларация и не отправка в ФНС', response.text)

    def test_new_lesson_is_served_only_when_its_actual_asset_is_approved(self):
        body = b'approved-test-media'
        digest = hashlib.sha256(body).hexdigest()
        name = f'lesson-12-tax.{digest}.mp4'
        (self.root/name).write_bytes(body)
        manifest = {'version': 1, 'approved': True, 'videos': {'lesson-12-tax': {'filename': name, 'sha256': digest, 'bytes': len(body)}}}
        (self.root/'manifest.json').write_text(json.dumps(manifest))
        html = self.client.get('/training').text
        self.assertEqual(html.count('<source '), 1)
        self.assertIn(f'src="/training/media/{name}"', html)
        duration = training.training_context()['training_lessons'][11]['duration_label']
        self.assertIn(f'Видео · {duration} · без озвучки', html)
        self.assertEqual(self.client.get('/training/media/'+name).content, body)
    def test_verified_public_manifest_serves_seekable_immutable_video(self):
        name, _ = self.published()
        self.assertIn(f'/training/media/{name}', self.client.get('/training').text)
        response = self.client.get('/training/media/'+name,headers={'Range':'bytes=0-31'})
        self.assertEqual(response.status_code,206)
        self.assertEqual(response.content,bytes(range(32)))
        self.assertEqual(response.headers['content-type'],'video/mp4')
        self.assertEqual(response.headers['content-range'],'bytes 0-31/128')
        self.assertIn('immutable',response.headers['cache-control'])
        self.assertEqual(response.headers['x-content-type-options'],'nosniff')

    def test_course_durations_use_minutes_and_seconds(self):
        lessons = training.training_context()['training_lessons']
        self.assertEqual(len(lessons), 16)
        for row in lessons:
            seconds = round(row['duration'])
            self.assertEqual(row['duration_label'], f'{seconds // 60}:{seconds % 60:02d}')
            self.assertGreater(row['duration'], 0)
    def test_narration_label_follows_approved_asset_not_catalog_or_missing_media(self):
        _, manifest = self.published()
        manifest['videos']['lesson-01-today']['narrated'] = True
        (self.root/'manifest.json').write_text(json.dumps(manifest))
        html = self.client.get('/training').text
        self.assertIn('Видео · 1:15 · с озвучкой', html)
        self.assertIn('Видео с русской озвучкой', html)
        self.assertNotIn('Видео без озвучки:', html)
        manifest['approved'] = False
        (self.root/'manifest.json').write_text(json.dumps(manifest))
        self.assertNotIn('с русской озвучкой', self.client.get('/training').text)
    def test_unlisted_files_and_manifest_are_never_served(self):
        (self.root/'private.mp4').write_bytes(b'not-approved')
        for name in ['private.mp4','manifest.json','unknown.'+'a'*64+'.mp4']:
            self.assertEqual(self.client.get('/training/media/'+name).status_code,404)
    def test_russian_subtitles_are_bound_to_the_approved_lesson(self):
        _, manifest=self.published()
        body=b'WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nLesson\n'
        digest=hashlib.sha256(body).hexdigest()
        name=f'lesson-01-today.{digest}.vtt'
        (self.root/name).write_bytes(body)
        manifest['videos']['lesson-01-today']['captions']={'filename':name,'sha256':digest,'bytes':len(body)}
        (self.root/'manifest.json').write_text(json.dumps(manifest))
        response=self.client.get('/training/media/'+name)
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.content,body)
        self.assertTrue(response.headers['content-type'].startswith('text/vtt'))
        self.assertIn(f'src="/training/media/{name}"',self.client.get('/training').text)
        self.assertIn('srclang="ru"',self.client.get('/training').text)
        self.assertNotIn('label="Русские инструкции" default',self.client.get('/training').text)
        (self.root/name).unlink()
        self.assertNotIn('<source ',self.client.get('/training').text)
    def test_unapproved_or_wrong_size_manifest_is_not_published(self):
        for change in ['approval','size','path']:
            name, manifest = self.published()
            if change=='approval': manifest['approved']=False
            elif change=='size': manifest['videos']['lesson-01-today']['bytes']=129
            else: manifest['videos']['lesson-01-today']['filename']='../private.mp4'
            (self.root/'manifest.json').write_text(json.dumps(manifest))
            self.assertEqual(self.client.get('/training/media/'+name).status_code,404)
            self.assertNotIn('<source ',self.client.get('/training').text)
