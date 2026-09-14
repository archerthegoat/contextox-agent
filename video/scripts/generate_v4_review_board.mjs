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
const frames = [0, 75, 120, 195, 225, 315, 360, 390, 465, 525, 630, 660, 705, 750, 770, 780, 795, 801, 810, 900, 990, 1005, 1050, 1110, 1185, 1260, 1290, 1320, 1335, 1348];
const columns = 5;
const rows = Math.ceil(frames.length / columns);
const cellWidth = 300;
const cellHeight = 169;
const gap = 8;
const labelHeight = 26;
const margin = 12;

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
    '-vf', `scale=${cellWidth}:${cellHeight}`,
    outputPath,
  ], {quiet: true, env: ffmpegEnvironment});
  return {
    frame,
    data: readFileSync(outputPath).toString('base64'),
  };
});

const width = margin * 2 + columns * cellWidth + (columns - 1) * gap;
const height = margin * 2 + rows * (cellHeight + labelHeight) + (rows - 1) * gap;
const canvasSize = Math.max(width, height);
const quickLookSize = Math.min(canvasSize, 1600);
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
      <text x="${x + 10}" y="${y + cellHeight + 18}" fill="#d8dce7" font-family="Arial, PingFang SC, sans-serif" font-size="13">${seconds}s · frame ${frame}</text>
    </g>`;
}).join('');

const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${canvasSize}" height="${canvasSize}" viewBox="0 0 ${canvasSize} ${canvasSize}">
  <rect width="${canvasSize}" height="${canvasSize}" fill="#08080a"/>
  ${cards}
</svg>`;

writeFileSync(svgPath, svg);
// Quick Look fills a square thumbnail from the SVG height. A square source canvas
// prevents the fifth column from being cropped; the bound keeps previews portable.
run('/usr/bin/qlmanage', ['-t', '-s', String(quickLookSize), '-o', outputRoot, svgPath], {quiet: true});
renameSync(quickLookPath, pngPath);
rmSync(tempRoot, {recursive: true, force: true});
console.log(`generated ${pngPath}`);
