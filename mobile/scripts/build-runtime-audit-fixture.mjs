// Test-only fixture: bundle the real frontend download implementation, never a copy.
// Run after npm ci in mh-redesign-lab. The output belongs only to the test APK.
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve, dirname } from 'node:path';
const mobile = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const frontend = resolve(mobile, '../mh-redesign-lab');
const { build } = createRequire(resolve(frontend, 'package.json'))('esbuild');
const assets = resolve(mobile, 'android/app/src/androidTest/assets');
await mkdir(assets, { recursive: true });
await build({ stdin: { contents: "import {saveBlob} from './src/download.ts'; window.__auditSaveBlob=saveBlob;", resolveDir: frontend }, bundle: true, format: 'iife', platform: 'browser', outfile: resolve(assets, 'runtime-download-fixture.js') });
await writeFile(resolve(assets, 'runtime-download-source.txt'), 'mh-redesign-lab/src/download.ts\n');
console.log('Bundled real saveBlob into Android test assets (release assets unchanged).');
