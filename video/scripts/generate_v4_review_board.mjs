import {mkdirSync, readFileSync, renameSync, rmSync, writeFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';

const videoRoot = resolve(fileURLToPath(new URL('../', import.meta.url)));
const repositoryRoot = resolve(videoRoot, '..');
const outputRoot = resolve(repositoryRoot, 'output', 'video');
const inputPath = resolve(outputRoot, 'contextox-demo-1.0.0-product-film-v4-a-candidate.mp4');
const compositor = resolve(videoRoot, 'node_modules', '@remotion', 'compositor-darwin-arm64');
const ffmpeg = resolve(compositor, 'ffmpeg');
const ffmpegEnvironment = {...process.env, DYLD_LIBRARY_PATH: compositor};
const tempRoot = '/private/tmp/contextox-v4-a-review';
const svgPath = resolve(tempRoot, 'contextox-demo-1.0.0-product-film-v4-a-review-board.svg');
const quickLookPath = resolve(outputRoot, 'contextox-demo-1.0.0-product-film-v4-a-review-board.svg.png');
const pngPath = resolve(outputRoot, 'contextox-demo-1.0.0-product-film-v4-a-review-board.png');
const frames = [0, 75, 120, 195, 225, 315, 360, 390, 465, 525, 630, 705, 750, 770, 795, 810, 900, 990, 1005, 1050, 1110, 1185, 1260, 1320];

const run = (command, args, options = {}) => {
  const result = spawnSync(command, args, {
    cwd: videoRoot,
    stdio: options.quiet ? 'ignore' : 'inherit',
    env: options.env ?? process.env,
  });
  if (result.error) throw result.error;
  if (result.status !== 0) process.exit(result.status ?? 1);
};

mkdirSync(tempRoot, {recursive: true});
const images = frames.map((frame) => {
  const outputPath = resolve(tempRoot, `${String(frame).padStart(4, '0')}.jpg`);
  run(ffmpeg, [
    '-hide_banner',
    '-loglevel', 'error',
    '-y',
    '-ss', (frame / 30).toFixed(3),
    '-i', inputPath,
    '-frames:v', '1',
    '-vf', 'scale=384:216',
    outputPath,
  ], {quiet: true, env: ffmpegEnvironment});
  return {
    frame,
    data: readFileSync(outputPath).toString('base64'),
  };
});

const columns = 5;
const rows = 5;
const cellWidth = 384;
const cellHeight = 216;
const gap = 12;
const labelHeight = 32;
const margin = 20;
const width = margin * 2 + columns * cellWidth + (columns - 1) * gap;
const height = margin * 2 + rows * (cellHeight + labelHeight) + (rows - 1) * gap;
const cards = images.map(({frame, data}, index) => {
  const column = index % columns;
  const row = Math.floor(index / columns);
  const x = margin + column * (cellWidth + gap);
  const y = margin + row * (cellHeight + labelHeight + gap);
  const seconds = (frame / 30).toFixed(1);
  return `
    <g>
      <image href="data:image/jpeg;base64,${data}" x="${x}" y="${y}" width="${cellWidth}" height="${cellHeight}"/>
      <rect x="${x}" y="${y + cellHeight}" width="${cellWidth}" height="${labelHeight}" fill="#121318"/>
      <text x="${x + 12}" y="${y + cellHeight + 22}" fill="#d8dce7" font-family="Arial, PingFang SC, sans-serif" font-size="15">${seconds}s · frame ${frame}</text>
    </g>`;
}).join('');

const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}">
  <rect width="${width}" height="${height}" fill="#08080a"/>
  ${cards}
</svg>`;

writeFileSync(svgPath, svg);
run('/usr/bin/qlmanage', ['-t', '-s', String(width), '-o', outputRoot, svgPath], {quiet: true});
renameSync(quickLookPath, pngPath);
rmSync(tempRoot, {recursive: true, force: true});
console.log(`generated ${pngPath}`);
