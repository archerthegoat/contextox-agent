import React from 'react';
import {
  AbsoluteFill,
  Easing,
  interpolate,
  useCurrentFrame,
} from 'remotion';
import {AppSequence} from './video';

const ease = Easing.bezier(0.16, 1, 0.3, 1);

const valueAt = (frame: number, input: number[], output: number[]) =>
  interpolate(frame, input, output, {
    easing: ease,
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

const sourceFrameAt = (frame: number) =>
  valueAt(
    frame,
    [0, 89, 90, 194, 195, 299, 300, 349, 350, 379, 380, 419],
    [284, 449, 450, 659, 660, 869, 870, 1079, 1080, 1130, 1170, 1170],
  );

const beatAt = (frame: number) => {
  if (frame < 90) return {step: '01', start: 0, label: '选三份资料，说出目标'};
  if (frame < 195) return {step: '02', start: 90, label: 'Agent 找出会改变结果的问题'};
  if (frame < 300) return {step: '03', start: 195, label: '关键规则，由人确认'};
  if (frame < 350) return {step: '04', start: 300, label: '回答整理成候选定义'};
  return {step: '04', start: 350, label: '变化、出处、未知事项，都能回看'};
};

export const ContextOxReadmeDemo: React.FC = () => {
  const frame = useCurrentFrame();
  const sourceFrame = sourceFrameAt(frame);
  const beat = beatAt(frame);
  const cameraScale = valueAt(
    frame,
    [0, 75, 90, 180, 195, 285, 300, 345, 360, 419],
    [1.055, 1.075, 1.09, 1.105, 1.13, 1.16, 1.09, 1.105, 1.08, 1.08],
  );
  const cameraX = valueAt(
    frame,
    [0, 75, 90, 180, 195, 285, 300, 345, 360, 419],
    [-58, -72, -62, -72, -105, -112, -36, -42, -18, -18],
  );
  const opacity = valueAt(frame, [0, 8, 407, 419], [0, 1, 1, 0]);
  const captionIn = valueAt(frame - beat.start, [0, 8], [0, 1]);

  return (
    <AbsoluteFill style={{overflow: 'hidden', background: '#F5F7FA', opacity}}>
      <AbsoluteFill
        style={{
          scale: cameraScale,
          translate: `${cameraX}px 0`,
          transformOrigin: 'center center',
        }}
      >
        <AppSequence frame={sourceFrame} showCaption={false} showDisclosure={false} />
      </AbsoluteFill>

      <div
        style={{
          position: 'absolute',
          left: 74,
          top: 22,
          display: 'flex',
          alignItems: 'center',
          gap: 12,
          padding: '8px 13px',
          border: '1px solid rgba(22,116,243,.22)',
          borderRadius: 7,
          background: 'rgba(245,247,250,.93)',
          color: '#1674F3',
          fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", sans-serif',
          fontSize: 18,
          fontWeight: 750,
          letterSpacing: 0.3,
          boxShadow: '0 10px 28px rgba(19,36,58,.08)',
        }}
      >
        界面演示 · 对应当前工作台 · 公开合成资料
      </div>

      <div
        style={{
          position: 'absolute',
          left: 116,
          right: 116,
          bottom: 34,
          display: 'grid',
          gridTemplateColumns: '58px 1fr auto',
          alignItems: 'center',
          gap: 18,
          minHeight: 76,
          padding: '12px 22px',
          border: '1px solid #BFCBDA',
          borderRadius: 9,
          background: 'rgba(255,255,255,.96)',
          color: '#13243A',
          boxShadow: '0 18px 50px rgba(19,36,58,.16)',
          fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", sans-serif',
          opacity: captionIn,
          translate: `0 ${(1 - captionIn) * 12}px`,
        }}
      >
        <strong
          style={{
            display: 'grid',
            width: 48,
            height: 48,
            placeItems: 'center',
            borderRadius: 7,
            background: '#1674F3',
            color: 'white',
            fontSize: 20,
          }}
        >
          {beat.step}
        </strong>
        <strong style={{fontSize: 32, lineHeight: 1.25, letterSpacing: -0.5}}>{beat.label}</strong>
        <span style={{color: '#607086', fontSize: 16}}>候选结果 · 人可复核</span>
      </div>
    </AbsoluteFill>
  );
};
