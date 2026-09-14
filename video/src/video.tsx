import React, {type CSSProperties, type ReactNode} from 'react';
import {
  AbsoluteFill,
  Audio,
  Easing,
  Img,
  Sequence,
  interpolate,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';

const palette = {
  navy: '#0D1B2E',
  ink: '#13243A',
  muted: '#607086',
  paper: '#F3EFE7',
  coolPaper: '#F5F7FA',
  surface: '#FFFFFF',
  line: '#DCE3EB',
  lineStrong: '#B9C5D3',
  blue: '#1674F3',
  marker: '#2C7DFF',
  blueSoft: '#EAF3FF',
  green: '#147D64',
  greenSoft: '#EAF7F2',
  amber: '#A76A08',
  amberSoft: '#FFF3D9',
  red: '#B8493E',
  redSoft: '#FFECE8',
};

const fontFamily =
  '-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Hiragino Sans GB", sans-serif';

const ease = Easing.bezier(0.16, 1, 0.3, 1);

const tween = (
  frame: number,
  from: number,
  to: number,
  outputFrom = 0,
  outputTo = 1,
) =>
  interpolate(frame, [from, to], [outputFrom, outputTo], {
    easing: ease,
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

const pulse = (frame: number, at: number, length = 7) =>
  interpolate(Math.abs(frame - at), [0, length], [1, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

const paperTexture: CSSProperties = {
  backgroundColor: palette.paper,
  backgroundImage:
    'radial-gradient(circle at 12% 18%, rgba(19,36,58,.055) 0 1px, transparent 1.5px), radial-gradient(circle at 82% 66%, rgba(19,36,58,.04) 0 1px, transparent 1.5px), linear-gradient(114deg, rgba(255,255,255,.55), transparent 52%)',
  backgroundSize: '19px 19px, 27px 27px, 100% 100%',
};

const darkTexture: CSSProperties = {
  backgroundColor: palette.navy,
  backgroundImage:
    'linear-gradient(rgba(255,255,255,.045) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,.045) 1px, transparent 1px), radial-gradient(circle at 76% 26%, rgba(22,116,243,.27), transparent 38%)',
  backgroundSize: '72px 72px, 72px 72px, 100% 100%',
};

const EditorialLabel: React.FC<{children: ReactNode; dark?: boolean}> = ({
  children,
  dark = false,
}) => (
  <div
    style={{
      position: 'absolute',
      left: 72,
      top: 38,
      zIndex: 80,
      padding: '8px 12px',
      border: `1px solid ${dark ? 'rgba(255,255,255,.2)' : palette.lineStrong}`,
      background: dark ? 'rgba(13,27,46,.84)' : 'rgba(255,255,255,.88)',
      color: dark ? '#D9E7F8' : palette.blue,
      fontSize: 15,
      fontWeight: 780,
      letterSpacing: 0.7,
    }}
  >
    {children}
  </div>
);

const Disclosure: React.FC<{dark?: boolean}> = ({dark = false}) => (
  <div
    style={{
      position: 'absolute',
      right: 72,
      top: 40,
      zIndex: 80,
      color: dark ? '#9FB0C5' : palette.muted,
      fontSize: 14,
      fontWeight: 650,
      letterSpacing: 0.2,
    }}
  >
    公开合成 Demo · 对应当前 Workbench
  </div>
);

const SceneCaption: React.FC<{
  index: string;
  children: ReactNode;
  dark?: boolean;
}> = ({index, children, dark = false}) => (
  <div
    style={{
      position: 'absolute',
      left: 76,
      right: 76,
      bottom: 30,
      zIndex: 90,
      display: 'flex',
      alignItems: 'baseline',
      gap: 18,
      color: dark ? '#F7FAFF' : palette.ink,
      textShadow: dark ? '0 2px 20px rgba(0,0,0,.45)' : '0 2px 18px rgba(255,255,255,.8)',
    }}
  >
    <span
      style={{
        color: palette.marker,
        fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
        fontSize: 22,
        fontWeight: 850,
      }}
    >
      {index}
    </span>
    <strong style={{fontSize: 36, lineHeight: 1.15, letterSpacing: -1.2}}>{children}</strong>
  </div>
);

const orders = [
  ['A-101', '华东', '120', '已支付', '08-02'],
  ['A-102', '华南', '80.5', '已支付', '08-02'],
  ['A-103', '华东', '50', '已退款', '08-03'],
  ['A-104', '华东', '199', '待支付', '—'],
  ['A-105', '华南', '60', '已支付', '08-03'],
  ['A-106', '华东', '320', '已支付', '08-04'],
] as const;

const toneFor = (status: string) =>
  status === '已退款'
    ? {background: palette.redSoft, color: palette.red}
    : status === '待支付'
      ? {background: palette.amberSoft, color: palette.amber}
      : {background: palette.greenSoft, color: palette.green};

const OrdersTable: React.FC<{
  frame: number;
  dark?: boolean;
  large?: boolean;
}> = ({frame, dark = false, large = false}) => (
  <div
    style={{
      overflow: 'hidden',
      border: `1px solid ${dark ? 'rgba(255,255,255,.2)' : palette.lineStrong}`,
      background: dark ? 'rgba(255,255,255,.065)' : palette.surface,
      boxShadow: dark ? '0 26px 70px rgba(0,0,0,.25)' : '0 22px 55px rgba(25,42,65,.11)',
    }}
  >
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: '1.2fr 1fr 1fr 1.2fr 1fr',
        gap: 10,
        padding: large ? '20px 24px' : '15px 20px',
        background: dark ? 'rgba(255,255,255,.085)' : '#E8EEF6',
        color: dark ? '#AFBED2' : palette.muted,
        fontSize: large ? 20 : 16,
        fontWeight: 780,
      }}
    >
      <span>订单</span><span>地区</span><span>金额</span><span>状态</span><span>支付时间</span>
    </div>
    {orders.map((row, index) => {
      const reveal = tween(frame, index * 5, index * 5 + 10);
      return (
        <div
          key={row[0]}
          style={{
            display: 'grid',
            gridTemplateColumns: '1.2fr 1fr 1fr 1.2fr 1fr',
            alignItems: 'center',
            gap: 10,
            minHeight: large ? 76 : 58,
            padding: large ? '10px 24px' : '8px 20px',
            borderTop: `1px solid ${dark ? 'rgba(255,255,255,.11)' : palette.line}`,
            color: dark ? '#F6F9FD' : palette.ink,
            fontSize: large ? 22 : 18,
            opacity: reveal,
            transform: `translateX(${(1 - reveal) * -26}px)`,
          }}
        >
          <strong>{row[0]}</strong>
          <span>{row[1]}</span>
          <span style={{fontVariantNumeric: 'tabular-nums'}}>{row[2]}</span>
          <span
            style={{
              justifySelf: 'start',
              padding: '5px 9px',
              borderRadius: 4,
              fontSize: large ? 17 : 14,
              fontWeight: 760,
              ...toneFor(row[3]),
            }}
          >
            {row[3]}
          </span>
          <span style={{color: dark ? '#B9C7D8' : palette.muted}}>{row[4]}</span>
        </div>
      );
    })}
  </div>
);

const MarkerStroke: React.FC<{
  frame: number;
  start: number;
  path: string;
  strokeWidth?: number;
}> = ({frame, start, path, strokeWidth = 8}) => {
  const draw = tween(frame, start, start + 10, 100, 0);
  return (
    <path
      d={path}
      fill="none"
      stroke={palette.marker}
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      pathLength={100}
      strokeDasharray={100}
      strokeDashoffset={draw}
      opacity={0.88}
    />
  );
};

const NumberBarrage: React.FC = () => {
  const frame = useCurrentFrame();
  const flashes = Math.max(pulse(frame, 18, 4), pulse(frame, 54, 4), pulse(frame, 90, 4));
  const values = [
    {value: '689', note: '全部状态直接相加', at: 18},
    {value: '440', note: '只统计已支付', at: 54},
    {value: '390', note: '已支付再扣退款', at: 90},
  ];

  return (
    <AbsoluteFill style={{...darkTexture, fontFamily, color: '#F7FAFF'}}>
      <EditorialLabel dark>订单金额 · 公开合成案例</EditorialLabel>
      <div
        style={{
          position: 'absolute',
          left: 84,
          top: 128,
          width: 1050,
          transform: `rotate(${-1.2 + tween(frame, 0, 24, 0.7, 0)}deg)`,
        }}
      >
        <OrdersTable frame={frame} dark />
      </div>
      <div style={{position: 'absolute', right: 84, top: 145, width: 590, display: 'grid', gap: 14}}>
        {values.map(({value, note, at}, index) => {
          const amount = spring({
            frame: frame - at,
            fps: 30,
            config: {damping: 13, stiffness: 210, mass: 0.7},
          });
          return (
            <div
              key={value}
              style={{
                position: 'relative',
                minHeight: 190,
                padding: '18px 28px',
                border: `1px solid ${index === 2 ? '#4E9CFF' : 'rgba(255,255,255,.2)'}`,
                background: index === 2 ? 'rgba(22,116,243,.24)' : 'rgba(255,255,255,.06)',
                opacity: amount,
                transform: `translateX(${(1 - amount) * 240}px) scale(${0.84 + amount * 0.16})`,
              }}
            >
              <strong style={{display: 'block', fontSize: 100, lineHeight: 0.95, letterSpacing: -7}}>
                {value}
                <small style={{marginLeft: 12, fontSize: 28, letterSpacing: 0}}>元</small>
              </strong>
              <span style={{display: 'block', marginTop: 14, color: '#B9C8DB', fontSize: 20}}>{note}</span>
            </div>
          );
        })}
      </div>
      <div
        style={{
          position: 'absolute',
          inset: 0,
          zIndex: 120,
          background: 'white',
          opacity: flashes * 0.96,
          pointerEvents: 'none',
        }}
      />
      <SceneCaption index="01" dark>同一份数据，三个答案？</SceneCaption>
      <div style={{position: 'absolute', right: 84, bottom: 42, color: '#899BB1', fontSize: 13}}>
        数字用于解释规则差异，不是产品计算结果
      </div>
    </AbsoluteFill>
  );
};

const FreezeRules: React.FC = () => {
  const frame = useCurrentFrame();
  const zoom = tween(frame, 0, 15, 1.16, 1);
  const tags = [
    {title: '哪些算进去', body: '待支付算不算？', at: 26, left: 1170, top: 250},
    {title: '退款怎么处理', body: '从哪里扣？', at: 56, left: 1260, top: 480},
    {title: '按什么时间', body: '下单还是支付？', at: 86, left: 1120, top: 710},
  ];
  return (
    <AbsoluteFill style={{...paperTexture, fontFamily, color: palette.ink}}>
      <EditorialLabel>规则冻结 · 把歧义写出来</EditorialLabel>
      <h1 style={{position: 'absolute', left: 84, top: 90, margin: 0, fontSize: 64, letterSpacing: -3}}>
        差的不是公式，<span style={{color: palette.marker}}>是规则。</span>
      </h1>
      <div
        style={{
          position: 'absolute',
          left: 84,
          top: 230,
          width: 1010,
          transform: `scale(${zoom})`,
          transformOrigin: '42% 42%',
        }}
      >
        <OrdersTable frame={120} large />
        <svg style={{position: 'absolute', inset: 0, width: '100%', height: '100%', overflow: 'visible'}} viewBox="0 0 1010 580">
          <MarkerStroke frame={frame} start={22} path="M650 12 C780 0 908 6 1000 18" />
          <MarkerStroke frame={frame} start={52} path="M600 276 C695 250 820 248 920 278 C825 305 692 307 600 276" />
          <MarkerStroke frame={frame} start={82} path="M816 14 C865 4 932 7 998 24" />
        </svg>
      </div>
      {tags.map((tag, index) => {
        const amount = spring({
          frame: frame - tag.at,
          fps: 30,
          config: {damping: 16, stiffness: 170, mass: 0.7},
        });
        return (
          <div
            key={tag.title}
            style={{
              position: 'absolute',
              left: tag.left,
              top: tag.top,
              width: 520,
              padding: '20px 24px',
              borderLeft: `7px solid ${palette.marker}`,
              background: 'rgba(255,255,255,.9)',
              boxShadow: '0 20px 50px rgba(20,38,60,.13)',
              opacity: amount,
              transform: `translateX(${(1 - amount) * 55}px) rotate(${index % 2 ? 0.7 : -0.7}deg)`,
            }}
          >
            <small style={{color: palette.blue, fontSize: 18, fontWeight: 850}}>{tag.title}</small>
            <strong style={{display: 'block', marginTop: 7, fontSize: 31}}>{tag.body}</strong>
          </div>
        );
      })}
      <SceneCaption index="02">先说清：哪些算进去、怎么处理、按什么时间</SceneCaption>
    </AbsoluteFill>
  );
};

const Positioning: React.FC = () => {
  const frame = useCurrentFrame();
  const leftIn = tween(frame, 0, 16);
  const rightIn = tween(frame, 14, 30);
  const underline = tween(frame, 42, 52, 100, 0);
  return (
    <AbsoluteFill style={{...darkTexture, fontFamily, color: '#F7FAFF'}}>
      <EditorialLabel dark>不是替代 · 是关注重点不同</EditorialLabel>
      <div style={{position: 'absolute', left: 110, right: 110, top: 165, bottom: 150, display: 'grid', gridTemplateColumns: '1fr 120px 1fr', alignItems: 'center'}}>
        <div style={{opacity: leftIn, transform: `translateX(${(1 - leftIn) * -55}px)`}}>
          <span style={{color: '#97A9BE', fontSize: 19, fontWeight: 750}}>CODEX 等通用 AGENT</span>
          <h2 style={{margin: '24px 0 18px', fontSize: 66, lineHeight: 1.08, letterSpacing: -3}}>理解要求，<br />把任务执行出来</h2>
          <p style={{margin: 0, color: '#B7C5D7', fontSize: 24, lineHeight: 1.6}}>写代码、查资料、操作工具，<br />在明确目标下完成工作。</p>
        </div>
        <div style={{height: 420, borderLeft: '1px solid rgba(255,255,255,.18)', justifySelf: 'center'}} />
        <div style={{position: 'relative', opacity: rightIn, transform: `translateX(${(1 - rightIn) * 55}px)`}}>
          <span style={{color: '#81B5FF', fontSize: 19, fontWeight: 850}}>数契 CONTEXTOX</span>
          <h2 style={{margin: '24px 0 18px', fontSize: 66, lineHeight: 1.08, letterSpacing: -3}}>找到歧义，<br /><span style={{color: '#78B0FF'}}>把业务定义说清楚</span></h2>
          <svg style={{position: 'absolute', left: 0, top: 180, width: 650, height: 45}} viewBox="0 0 650 45">
            <path d="M8 26 C165 12 316 33 642 17" pathLength={100} stroke="#4D9AFF" strokeWidth="10" fill="none" strokeLinecap="round" strokeDasharray={100} strokeDashoffset={underline} />
          </svg>
          <p style={{margin: 0, color: '#D0DBE8', fontSize: 24, lineHeight: 1.6}}>证据、问题、人的决定和变化，<br />留在同一条可回看的路径里。</p>
        </div>
      </div>
      <SceneCaption index="03" dark>通用 Agent 负责执行；数契聚焦业务定义</SceneCaption>
    </AbsoluteFill>
  );
};

const RealWorkbench: React.FC<{
  asset: string;
  opacity?: number;
  scale?: number;
  origin?: string;
  x?: number;
  y?: number;
  rotate?: number;
}> = ({asset, opacity = 1, scale = 1, origin = 'center', x = 0, y = 0, rotate = 0}) => (
  <div
    style={{
      position: 'absolute',
      left: 80,
      top: 45,
      width: 1760,
      height: 990,
      overflow: 'hidden',
      border: `1px solid ${palette.lineStrong}`,
      borderRadius: 16,
      background: palette.surface,
      boxShadow: '0 32px 90px rgba(15,31,51,.23)',
      opacity,
      transform: `translate(${x}px, ${y}px) rotate(${rotate}deg) scale(${scale})`,
      transformOrigin: origin,
    }}
  >
    <Img src={staticFile(asset)} style={{width: '100%', height: '100%', objectFit: 'cover'}} />
  </div>
);

const Cursor: React.FC<{x: number; y: number; down?: boolean; click?: number}> = ({
  x,
  y,
  down = false,
  click = 0,
}) => (
  <div style={{position: 'absolute', left: x, top: y, zIndex: 100, pointerEvents: 'none'}}>
    <div
      style={{
        position: 'absolute',
        left: -21,
        top: -21,
        width: 48,
        height: 48,
        border: `3px solid rgba(22,116,243,${0.8 * click})`,
        borderRadius: '50%',
        opacity: click,
        transform: `scale(${1 + (1 - click) * 1.05})`,
      }}
    />
    <div
      style={{
        width: 38,
        height: 48,
        background: palette.ink,
        clipPath: 'polygon(0 0, 0 92%, 27% 70%, 44% 100%, 60% 90%, 43% 64%, 76% 64%)',
        filter: 'drop-shadow(0 1px 1px white) drop-shadow(0 4px 7px rgba(0,0,0,.35))',
        transform: `scale(${down ? 0.84 : 1})`,
      }}
    />
  </div>
);

const bezierPoint = (
  amount: number,
  a: readonly [number, number],
  b: readonly [number, number],
  c: readonly [number, number],
  d: readonly [number, number],
) => {
  const inverse = 1 - amount;
  return [
    inverse ** 3 * a[0] + 3 * inverse ** 2 * amount * b[0] + 3 * inverse * amount ** 2 * c[0] + amount ** 3 * d[0],
    inverse ** 3 * a[1] + 3 * inverse ** 2 * amount * b[1] + 3 * inverse * amount ** 2 * c[1] + amount ** 3 * d[1],
  ] as const;
};

const WorkbenchInput: React.FC = () => {
  const frame = useCurrentFrame();
  const enterAmount = spring({frame, fps: 30, config: {damping: 18, stiffness: 125, mass: 0.8}});
  const cameraPush = frame < 160 ? tween(frame, 105, 145, 1, 1.075) : tween(frame, 160, 205, 1.075, 1.015);
  const screenshotSwap = tween(frame, 152, 168);
  const arc = tween(frame, 76, 138);
  const arcPoint = bezierPoint(arc, [180, 475], [480, 390], [980, 690], [1420, 855]);
  const sendArc = tween(frame, 168, 198);
  const sendPoint = bezierPoint(sendArc, arcPoint, [1500, 820], [1740, 900], [1840, 960]);
  const x = frame < 168 ? arcPoint[0] : sendPoint[0];
  const y = frame < 168 ? arcPoint[1] : sendPoint[1];
  const clicks = Math.max(pulse(frame, 30), pulse(frame, 54), pulse(frame, 78), pulse(frame, 145), pulse(frame, 198));
  const sourceHighlights = [30, 54, 78];
  return (
    <AbsoluteFill style={{...paperTexture, fontFamily, color: palette.ink}}>
      <EditorialLabel>真实产品界面 · 一轮任务开始</EditorialLabel>
      <Disclosure />
      <div
        style={{
          position: 'absolute',
          inset: 0,
          opacity: enterAmount,
          transform: `translateY(${(1 - enterAmount) * 40}px) scale(${cameraPush})`,
          transformOrigin: '82% 82%',
        }}
      >
        <RealWorkbench asset="textures/workbench-start.jpg" opacity={1 - screenshotSwap} />
        <RealWorkbench asset="textures/workbench-goal.jpg" opacity={screenshotSwap} />
        {sourceHighlights.map((at, index) => {
          const amount = tween(frame, at, at + 7) * (1 - tween(frame, at + 17, at + 27));
          return (
            <div
              key={at}
              style={{
                position: 'absolute',
                left: 93,
                top: 372 + index * 40,
                width: 205,
                height: 36,
                border: `3px solid rgba(44,125,255,${amount})`,
                background: `rgba(44,125,255,${amount * 0.08})`,
                borderRadius: 6,
              }}
            />
          );
        })}
        {frame >= 118 && frame < 166 ? (
          <div
            style={{
              position: 'absolute',
              left: 1215,
              top: 827,
              width: 610,
              height: 108,
              padding: '19px 20px',
              border: `2px solid ${palette.marker}`,
              borderRadius: 8,
              background: 'white',
              color: palette.ink,
              fontSize: 22,
              boxSizing: 'border-box',
            }}
          >
            {'按地区统计订单金额'.slice(0, Math.max(0, Math.floor((frame - 118) / 2.2)))}
            <span style={{display: 'inline-block', marginLeft: 2, width: 2, height: 24, background: palette.blue, opacity: frame % 12 < 7 ? 1 : 0}} />
          </div>
        ) : null}
      </div>
      <Cursor x={x} y={y} down={clicks > 0.58} click={clicks} />
      <SceneCaption index="04">选资料，说目标</SceneCaption>
    </AbsoluteFill>
  );
};

const QuestionRow: React.FC<{
  frame: number;
  at: number;
  number: string;
  title: string;
  impact: string;
  tone: 'amber' | 'red' | 'blue';
}> = ({frame, at, number, title, impact, tone}) => {
  const amount = spring({frame: frame - at, fps: 30, config: {damping: 17, stiffness: 150, mass: 0.75}});
  const color = tone === 'amber' ? palette.amber : tone === 'red' ? palette.red : palette.blue;
  const background = tone === 'amber' ? palette.amberSoft : tone === 'red' ? palette.redSoft : palette.blueSoft;
  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: '66px 1fr 330px',
        alignItems: 'center',
        gap: 22,
        padding: '20px 24px',
        border: `1px solid ${palette.line}`,
        borderLeft: `7px solid ${color}`,
        background: palette.surface,
        opacity: amount,
        transform: `translateY(${(1 - amount) * 32}px) scale(${0.98 + amount * 0.02})`,
      }}
    >
      <strong style={{color, fontSize: 30, fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace'}}>{number}</strong>
      <strong style={{fontSize: 25}}>{title}</strong>
      <span style={{padding: '12px 14px', background, color, fontSize: 18, fontWeight: 720}}>{impact}</span>
    </div>
  );
};

const AgentQuestions: React.FC = () => {
  const frame = useCurrentFrame();
  const panelIn = tween(frame, 0, 18);
  return (
    <AbsoluteFill style={{...paperTexture, fontFamily, color: palette.ink}}>
      <RealWorkbench asset="textures/workbench-goal.jpg" opacity={0.2} scale={1.18} origin="88% 45%" />
      <EditorialLabel>数契 Agent · 先把缺口问出来</EditorialLabel>
      <Disclosure />
      <div
        style={{
          position: 'absolute',
          left: 330,
          top: 185,
          width: 1260,
          minHeight: 0,
          padding: '34px 38px',
          border: `1px solid ${palette.lineStrong}`,
          background: 'rgba(250,252,255,.97)',
          boxShadow: '0 30px 85px rgba(20,38,60,.18)',
          opacity: panelIn,
          transform: `translateX(${(1 - panelIn) * 70}px)`,
          boxSizing: 'border-box',
        }}
      >
        <div style={{display: 'flex', alignItems: 'center', gap: 15, marginBottom: 24}}>
          <Img src={staticFile('contextox-mark.png')} style={{width: 46, height: 46}} />
          <div><strong style={{display: 'block', fontSize: 24}}>这些回答会直接改变结果</strong><span style={{color: palette.muted, fontSize: 17}}>所以 Agent 不替你猜。</span></div>
        </div>
        <div style={{display: 'grid', gap: 13}}>
          <QuestionRow frame={frame} at={36} number="01" title="待支付订单算不算？" impact="会影响 199 元是否进入" tone="amber" />
          <QuestionRow frame={frame} at={69} number="02" title="退款从哪里扣？" impact="会影响华东是否减 50 元" tone="red" />
          <QuestionRow frame={frame} at={102} number="03" title="按哪个时间归属？" impact="会改变跨天、跨月口径" tone="blue" />
        </div>
      </div>
      <SceneCaption index="05">Agent 找到会改变结果的问题</SceneCaption>
    </AbsoluteFill>
  );
};

const AnswerRow: React.FC<{
  frame: number;
  at: number;
  label: string;
  answer: string;
}> = ({frame, at, label, answer}) => {
  const checked = tween(frame, at, at + 8);
  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: '54px 235px 1fr',
        alignItems: 'center',
        minHeight: 92,
        padding: '0 22px',
        borderTop: `1px solid ${palette.line}`,
        background: checked > 0.2 ? '#F8FBFF' : 'white',
      }}
    >
      <span
        style={{
          display: 'grid',
          placeItems: 'center',
          width: 31,
          height: 31,
          border: `2px solid ${checked > 0.4 ? palette.blue : palette.lineStrong}`,
          borderRadius: '50%',
          background: checked > 0.4 ? palette.blue : 'white',
          color: 'white',
          fontSize: 19,
          fontWeight: 900,
          transform: `scale(${0.9 + checked * 0.1})`,
        }}
      >
        {checked > 0.4 ? '✓' : ''}
      </span>
      <span style={{color: palette.muted, fontSize: 18}}>{label}</span>
      <strong style={{fontSize: 23}}>{answer}</strong>
    </div>
  );
};

const HumanConfirmation: React.FC = () => {
  const frame = useCurrentFrame();
  const impactAt = 126;
  const zoom = frame < impactAt ? 1 : frame < impactAt + 6 ? tween(frame, impactAt, impactAt + 6, 1, 1.12) : tween(frame, impactAt + 6, impactAt + 21, 1.12, 1.025);
  const shakeAmount = tween(frame, impactAt + 6, impactAt + 24, 1, 0);
  const shakeX = Math.sin((frame - impactAt) * 2.3) * 9 * shakeAmount;
  const shakeY = Math.cos((frame - impactAt) * 1.7) * 5 * shakeAmount;
  const cursorY = frame < 46 ? 374 : frame < 78 ? 466 : frame < 110 ? 558 : 752;
  const cursorX = frame < 110 ? 470 : 1435;
  const click = Math.max(pulse(frame, 32), pulse(frame, 64), pulse(frame, 96), pulse(frame, impactAt));
  return (
    <AbsoluteFill style={{...paperTexture, fontFamily, color: palette.ink}}>
      <RealWorkbench asset="textures/workbench-goal.jpg" opacity={0.16} />
      <EditorialLabel>人的决定 · 用大白话确认</EditorialLabel>
      <Disclosure />
      <div
        style={{
          position: 'absolute',
          left: 300,
          top: 175,
          width: 1320,
          height: 640,
          overflow: 'hidden',
          border: `1px solid ${palette.lineStrong}`,
          borderRadius: 14,
          background: 'white',
          boxShadow: '0 32px 85px rgba(20,38,60,.2)',
          transform: `translate(${shakeX}px, ${shakeY}px) scale(${zoom})`,
        }}
      >
        <div style={{padding: '26px 28px 22px'}}>
          <small style={{color: palette.blue, fontSize: 17, fontWeight: 820}}>核对回答</small>
          <h2 style={{margin: '7px 0 0', fontSize: 34}}>以下规则确认后，才会进入候选定义</h2>
        </div>
        <AnswerRow frame={frame} at={28} label="纳入哪些订单" answer="只统计已支付订单" />
        <AnswerRow frame={frame} at={60} label="退款怎么处理" answer="从原订单地区扣除" />
        <AnswerRow frame={frame} at={92} label="按哪个时间" answer="按支付时间归属" />
        <button
          type="button"
          style={{
            position: 'absolute',
            right: 28,
            bottom: 30,
            width: 315,
            height: 66,
            border: 0,
            borderRadius: 8,
            background: palette.blue,
            color: 'white',
            fontFamily,
            fontSize: 21,
            fontWeight: 820,
            transform: `scale(${click > 0.5 && frame >= 115 ? 0.96 : 1})`,
          }}
        >
          确认并继续
        </button>
      </div>
      <Cursor x={cursorX} y={cursorY} down={click > 0.55} click={click} />
      <SceneCaption index="06">关键规则，人来定</SceneCaption>
    </AbsoluteFill>
  );
};

const CandidateDefinition: React.FC = () => {
  const frame = useCurrentFrame();
  const morph = tween(frame, 0, 38);
  const overshoot = spring({frame, fps: 30, config: {damping: 17, stiffness: 115, mass: 0.8}});
  const x = interpolate(morph, [0, 1], [1260, 255]);
  const y = interpolate(morph, [0, 1], [720, 205]);
  const width = interpolate(morph, [0, 1], [315, 820]);
  const height = interpolate(morph, [0, 1], [66, 340]);
  const radius = interpolate(morph, [0, 1], [8, 15]);
  const details = tween(frame, 32, 52);
  const travel = tween(frame, 70, 122);
  return (
    <AbsoluteFill style={{...paperTexture, fontFamily, color: palette.ink}}>
      <RealWorkbench asset="textures/workbench-candidate.jpg" opacity={0.34} scale={1 + travel * 0.045} origin="42% 45%" x={-travel * 55} />
      <EditorialLabel>候选成果 · 回答进入定义</EditorialLabel>
      <Disclosure />
      <div
        style={{
          position: 'absolute',
          left: x,
          top: y,
          width,
          height,
          overflow: 'hidden',
          border: `2px solid ${palette.blue}`,
          borderRadius: radius,
          background: morph < 0.38 ? palette.blue : 'rgba(255,255,255,.97)',
          color: morph < 0.38 ? 'white' : palette.ink,
          boxShadow: '0 28px 80px rgba(20,60,105,.22)',
          transform: `scale(${0.98 + overshoot * 0.02})`,
        }}
      >
        {morph < 0.42 ? (
          <div style={{display: 'grid', placeItems: 'center', width: '100%', height: '100%', fontSize: 21, fontWeight: 820}}>确认并继续</div>
        ) : (
          <div style={{padding: '28px 32px', opacity: details}}>
            <small style={{color: palette.blue, fontSize: 17, fontWeight: 850}}>候选字段</small>
            <h2 style={{margin: '10px 0 8px', fontSize: 43, letterSpacing: -1.8}}>地区净订单金额</h2>
            <p style={{margin: 0, color: palette.muted, fontSize: 20, lineHeight: 1.55}}>按客户地区汇总已支付金额，再扣除同一地区的退款金额。</p>
            <div style={{display: 'flex', gap: 9, marginTop: 25}}>
              {['只统计已支付', '退款按原地区扣除', '按支付时间'].map((text) => (
                <span key={text} style={{padding: '9px 12px', background: palette.greenSoft, color: palette.green, fontSize: 16, fontWeight: 730}}>{text}</span>
              ))}
            </div>
          </div>
        )}
      </div>
      <div
        style={{
          position: 'absolute',
          left: 1120,
          top: 230,
          width: 570,
          display: 'grid',
          gap: 14,
          opacity: tween(frame, 78, 102),
          transform: `translateX(${(1 - tween(frame, 78, 102)) * 75}px)`,
        }}
      >
        <div style={{padding: '25px 26px', border: `1px solid ${palette.lineStrong}`, background: 'rgba(255,255,255,.96)'}}>
          <small style={{color: palette.blue, fontSize: 16, fontWeight: 820}}>候选关系</small>
          <strong style={{display: 'block', marginTop: 9, fontSize: 28}}>订单 ↔ 客户</strong>
          <span style={{display: 'block', marginTop: 8, color: palette.muted, fontSize: 18}}>通过客户标识连接，地区来自客户资料</span>
        </div>
        <div style={{padding: '25px 26px', border: `1px solid ${palette.lineStrong}`, background: 'rgba(255,255,255,.96)'}}>
          <small style={{color: palette.blue, fontSize: 16, fontWeight: 820}}>资料来源</small>
          <strong style={{display: 'block', marginTop: 9, fontSize: 24}}>订单表 · 客户表 · 业务说明</strong>
        </div>
        <div style={{padding: '18px 22px', borderLeft: `6px solid ${palette.amber}`, background: 'rgba(255,248,230,.97)'}}>
          <small style={{color: palette.amber, fontSize: 16, fontWeight: 820}}>仍未知</small>
          <strong style={{display: 'block', marginTop: 6, fontSize: 22}}>跨月退款边界仍待补充</strong>
        </div>
      </div>
      <SceneCaption index="07">回答变成候选定义</SceneCaption>
    </AbsoluteFill>
  );
};

const RoundChanges: React.FC = () => {
  const frame = useCurrentFrame();
  const rows = [
    {label: '规则变化', value: '纳入、退款、时间已经写清', color: palette.blue, background: palette.blueSoft, at: 12},
    {label: '证据位置', value: '3 份资料都能回到出处', color: palette.green, background: palette.greenSoft, at: 30},
    {label: '仍未知', value: '跨月退款边界还要补充', color: palette.amber, background: palette.amberSoft, at: 48},
  ];
  return (
    <AbsoluteFill style={{...paperTexture, fontFamily, color: palette.ink}}>
      <RealWorkbench asset="textures/workbench-candidate.jpg" opacity={0.22} scale={1.13} origin="45% 35%" />
      <EditorialLabel>本轮变化 · 不是一段消失的回复</EditorialLabel>
      <Disclosure />
      <div style={{position: 'absolute', left: 260, top: 180, width: 1400, padding: '36px 40px', border: `1px solid ${palette.lineStrong}`, background: 'rgba(255,255,255,.97)', boxShadow: '0 30px 85px rgba(20,38,60,.2)', boxSizing: 'border-box'}}>
        <div style={{display: 'flex', justifyContent: 'space-between', alignItems: 'end', marginBottom: 26}}>
          <div><small style={{color: palette.blue, fontSize: 17, fontWeight: 850}}>查看本轮变化</small><h2 style={{margin: '8px 0 0', fontSize: 39}}>这轮到底改变了什么？</h2></div>
          <span style={{padding: '9px 12px', background: palette.greenSoft, color: palette.green, fontSize: 17, fontWeight: 760}}>可以回看</span>
        </div>
        <div style={{display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 17}}>
          {rows.map((row) => {
            const amount = spring({frame: frame - row.at, fps: 30, config: {damping: 17, stiffness: 160, mass: 0.7}});
            return (
              <div key={row.label} style={{minHeight: 260, padding: '28px 27px', borderTop: `7px solid ${row.color}`, background: row.background, opacity: amount, transform: `translateY(${(1 - amount) * 38}px)`}}>
                <small style={{color: row.color, fontSize: 18, fontWeight: 850}}>{row.label}</small>
                <strong style={{display: 'block', marginTop: 26, fontSize: 30, lineHeight: 1.35}}>{row.value}</strong>
              </div>
            );
          })}
        </div>
      </div>
      <SceneCaption index="08">变化、出处、未知，都能回看</SceneCaption>
    </AbsoluteFill>
  );
};

const BrandMorph: React.FC = () => {
  const frame = useCurrentFrame();
  const morph = tween(frame, 0, 42);
  const markIn = tween(frame, 24, 48);
  const copyIn = tween(frame, 44, 62);
  const cardWidth = interpolate(morph, [0, 1], [1450, 180]);
  const cardHeight = interpolate(morph, [0, 1], [780, 180]);
  const cardX = interpolate(morph, [0, 1], [235, 1420]);
  const cardY = interpolate(morph, [0, 1], [135, 235]);
  return (
    <AbsoluteFill style={{...darkTexture, fontFamily, color: '#F7FAFF'}}>
      <div
        style={{
          position: 'absolute',
          left: cardX,
          top: cardY,
          width: cardWidth,
          height: cardHeight,
          overflow: 'hidden',
          border: '1px solid rgba(255,255,255,.2)',
          borderRadius: interpolate(morph, [0, 1], [18, 45]),
          opacity: 1 - markIn,
          transform: `rotate(${interpolate(morph, [0, 1], [0, -7])}deg)`,
          boxShadow: '0 30px 90px rgba(0,0,0,.32)',
        }}
      >
        <Img src={staticFile('textures/workbench-candidate.jpg')} style={{width: '100%', height: '100%', objectFit: 'cover'}} />
      </div>
      {[0, 1, 2, 3].map((index) => {
        const angle = [-34, 18, 74, 126][index];
        const radius = 52 + index * 17;
        const x = 1510 + Math.cos((angle * Math.PI) / 180) * radius * markIn;
        const y = 325 + Math.sin((angle * Math.PI) / 180) * radius * markIn;
        return (
          <div
            key={index}
            style={{
              position: 'absolute',
              left: interpolate(markIn, [0, 1], [cardX + cardWidth / 2, x]),
              top: interpolate(markIn, [0, 1], [cardY + cardHeight / 2, y]),
              width: interpolate(markIn, [0, 1], [40, 88 - index * 8]),
              height: interpolate(markIn, [0, 1], [24, 24 + index * 5]),
              borderRadius: 999,
              background: index === 3 ? '#E9F3FF' : palette.blue,
              opacity: markIn * (1 - tween(frame, 36, 52)),
              transform: `translate(-50%, -50%) rotate(${angle + morph * 55}deg)`,
            }}
          />
        );
      })}
      <Img
        src={staticFile('contextox-mark.png')}
        style={{
          position: 'absolute',
          right: 250,
          top: 205,
          width: 300,
          height: 300,
          opacity: markIn,
          transform: `scale(${0.8 + markIn * 0.2}) rotate(${(1 - markIn) * -12}deg)`,
          filter: 'drop-shadow(0 28px 75px rgba(22,116,243,.32))',
        }}
      />
      <div style={{position: 'absolute', left: 155, top: 250, width: 1030, opacity: copyIn, transform: `translateY(${(1 - copyIn) * 35}px)`}}>
        <span style={{color: '#82B6FF', fontSize: 20, fontWeight: 820}}>数契 CONTEXTOX · DEMO 1.0.0</span>
        <h1 style={{margin: '28px 0 0', fontSize: 82, lineHeight: 1.12, letterSpacing: -4.2}}>把表里的<br /><span style={{color: '#78B0FF'}}>业务意思说清楚</span></h1>
        <p style={{margin: '32px 0 0', color: '#C5D2E2', fontSize: 25}}>Agent 找证据 · 人确认关键规则 · 候选定义可以回看</p>
      </div>
      <div style={{position: 'absolute', left: 155, right: 155, bottom: 70, display: 'flex', justifyContent: 'space-between', color: '#9FB0C5', fontSize: 16, opacity: tween(frame, 58, 72)}}>
        <span>公开合成 Demo · 候选而非正式批准结果</span>
        <span>github.com/archerthegoat/contextox-agent</span>
      </div>
    </AbsoluteFill>
  );
};

/**
 * Compatibility surface for the existing README animation.
 * Its frame values follow the original Workbench timeline, while the texture is
 * now a real product capture instead of a second hand-built application shell.
 */
export const AppSequence: React.FC<{
  frame: number;
  showCaption?: boolean;
  showDisclosure?: boolean;
}> = ({frame, showCaption = true, showDisclosure = true}) => {
  const isCandidate = frame >= 870;
  const caption = frame < 450
    ? '选资料，说目标'
    : frame < 660
      ? 'Agent 找到会改变结果的问题'
      : frame < 870
        ? '关键规则，人来定'
        : frame < 1080
          ? '回答变成候选定义'
          : '变化、出处、未知，都能回看';
  return (
    <AbsoluteFill style={{...paperTexture, fontFamily, color: palette.ink}}>
      <RealWorkbench
        asset={isCandidate ? 'textures/workbench-candidate.jpg' : frame < 420 ? 'textures/workbench-start.jpg' : 'textures/workbench-goal.jpg'}
      />
      {showDisclosure ? <Disclosure /> : null}
      {frame >= 450 && frame < 870 ? (
        <div
          style={{
            position: 'absolute',
            left: 1040,
            top: 175,
            width: 700,
            padding: '24px 26px',
            border: `1px solid ${palette.lineStrong}`,
            borderLeft: `7px solid ${palette.blue}`,
            background: 'rgba(255,255,255,.96)',
            boxShadow: '0 22px 65px rgba(20,38,60,.18)',
          }}
        >
          <small style={{color: palette.blue, fontSize: 17, fontWeight: 820}}>{frame < 660 ? '需要确认的规则' : '人的回答'}</small>
          <strong style={{display: 'block', marginTop: 10, fontSize: 26}}>{frame < 660 ? '待支付、退款、时间会改变结果' : '只统计已支付 · 退款按原地区扣除 · 按支付时间'}</strong>
        </div>
      ) : null}
      {showCaption ? <SceneCaption index="·">{caption}</SceneCaption> : null}
    </AbsoluteFill>
  );
};

export const ContextOxProductFilm: React.FC = () => (
  <AbsoluteFill style={{background: palette.coolPaper, fontFamily}}>
    <Audio src={staticFile('audio/contextox-v3-jazz-mix.wav')} endAt={1350} />
    <Sequence from={0} durationInFrames={144}><NumberBarrage /></Sequence>
    <Sequence from={144} durationInFrames={144}><FreezeRules /></Sequence>
    <Sequence from={288} durationInFrames={108}><Positioning /></Sequence>
    <Sequence from={396} durationInFrames={216}><WorkbenchInput /></Sequence>
    <Sequence from={612} durationInFrames={180}><AgentQuestions /></Sequence>
    <Sequence from={792} durationInFrames={180}><HumanConfirmation /></Sequence>
    <Sequence from={972} durationInFrames={180}><CandidateDefinition /></Sequence>
    <Sequence from={1152} durationInFrames={90}><RoundChanges /></Sequence>
    <Sequence from={1242} durationInFrames={108}><BrandMorph /></Sequence>
  </AbsoluteFill>
);
