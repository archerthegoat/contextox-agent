import {existsSync, rmSync} from 'node:fs';
import {resolve} from 'node:path';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';

const videoRoot = resolve(fileURLToPath(new URL('../', import.meta.url)));
const repositoryRoot = resolve(videoRoot, '..');
const outputRoot = resolve(repositoryRoot, 'output', 'video');
const rawPath = resolve(outputRoot, 'contextox-demo-1.0.0-product-film-en-raw.mp4');
const finalPath = resolve(outputRoot, 'contextox-demo-1.0.0-product-film-en.mp4');
const remotion = resolve(videoRoot, 'node_modules', '.bin', 'remotion');
const compositor = resolve(videoRoot, 'node_modules', '@remotion', 'compositor-darwin-arm64');
const ffmpeg = resolve(compositor, 'ffmpeg');
const ffmpegEnvironment = {...process.env, DYLD_LIBRARY_PATH: compositor};
const normalizeOnly = process.argv.includes('--normalize-only');

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: videoRoot,
    stdio: options.capture ? ['ignore', 'pipe', 'pipe'] : 'inherit',
    encoding: options.capture ? 'utf8' : undefined,
    env: options.env ?? process.env,
  });
  if (result.error) throw result.error;
  if (result.status !== 0) {
    if (options.capture) process.stderr.write(result.stderr ?? '');
    process.exit(result.status ?? 1);
  }
  return result;
}

if (!normalizeOnly) {
  run(remotion, [
    'render', 'src/index.ts', 'ContextOxProductFilmV4AEn', rawPath,
    '--codec=h264', '--crf=18', '--audio-codec=aac',
  ]);
} else if (!existsSync(rawPath)) {
  throw new Error(`Raw English render not found: ${rawPath}`);
}

const analysis = run(ffmpeg, [
  '-hide_banner', '-i', rawPath, '-vn',
  '-af', 'loudnorm=I=-16:TP=-1.3:LRA=7:print_format=json',
  '-f', 'null', '-',
], {capture: true, env: ffmpegEnvironment}).stderr;
const match = analysis.match(/\{\s*"input_i"[\s\S]*?\}/);
if (!match) throw new Error('Could not read loudnorm analysis from the English raw render.');
const measured = JSON.parse(match[0]);
const filter = [
  'loudnorm=I=-16:TP=-1.3:LRA=7',
  `measured_I=${measured.input_i}`,
  `measured_TP=${measured.input_tp}`,
  `measured_LRA=${measured.input_lra}`,
  `measured_thresh=${measured.input_thresh}`,
  `offset=${measured.target_offset}`,
  'linear=true', 'print_format=summary',
].join(':');

run(ffmpeg, [
  '-hide_banner', '-loglevel', 'warning', '-y', '-i', rawPath,
  '-c:v', 'copy', '-af', filter, '-c:a', 'aac', '-b:a', '192k',
  '-ar', '48000', '-ac', '2', '-t', '45', finalPath,
], {env: ffmpegEnvironment});
if (!existsSync(finalPath)) throw new Error('Normalized English product film was not created.');
rmSync(rawPath);
console.log(`rendered ${finalPath}`);
