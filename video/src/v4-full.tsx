import React, {type CSSProperties, type ReactNode} from 'react';
import {
  AbsoluteFill,
  Audio,
  Easing,
  Img,
  Sequence,
  interpolate,
  staticFile,
  useCurrentFrame,
} from 'remotion';
import workbenchLayout from '../v4/workbench-layout.json';
import {ContextOxProductFilmV4HookAVisual} from './v4-hook';

const WIDTH = 1920;
const HEIGHT = 1080;
const FONT =
  '-apple-system, BlinkMacSystemFont, "SF Pro Display", "PingFang SC", "Microsoft YaHei", sans-serif';

const C = {
  navy: '#0D1B2E',
  paper: '#F4F1EA',
  surface: '#FFFFFF',
  ink: '#13243A',
  muted: '#667487',
  quiet: '#93A0B0',
  line: '#D7E0EA',
  lineStrong: '#B9C6D5',
  blue: '#1674F3',
  blueSoft: '#EAF3FF',
  green: '#147D64',
  greenSoft: '#EAF7F2',
  amber: '#A76A08',
  amberSoft: '#FFF2D4',
};

const clamp = {
  extrapolateLeft: 'clamp' as const,
  extrapolateRight: 'clamp' as const,
};
const easeOut = Easing.out(Easing.cubic);
const easeInOut = Easing.inOut(Easing.cubic);

function tween(
  frame: number,
  start: number,
  end: number,
  outputStart = 0,
  outputEnd = 1,
  easing = easeOut,
): number {
  return interpolate(frame, [start, end], [outputStart, outputEnd], {...clamp, easing});
}

function pulse(frame: number, at: number, duration = 6): number {
  return interpolate(Math.abs(frame - at), [0, duration], [1, 0], clamp);
}

type Point = {frame: number; x: number; y: number};

function pathPoint(frame: number, points: Point[]): {x: number; y: number} {
  if (frame <= points[0].frame) return points[0];
  const last = points[points.length - 1];
  if (frame >= last.frame) return last;
  const rightIndex = points.findIndex((point) => point.frame >= frame);
  const left = points[rightIndex - 1];
  const right = points[rightIndex];
  const amount = tween(frame, left.frame, right.frame, 0, 1, easeInOut);
  return {
    x: left.x + (right.x - left.x) * amount,
    y: left.y + (right.y - left.y) * amount,
  };
}

const dark: CSSProperties = {
  backgroundColor: C.navy,
  backgroundImage:
    'linear-gradient(rgba(255,255,255,.035) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,.035) 1px, transparent 1px)',
  backgroundSize: '72px 72px',
};

const Kicker: React.FC<{children: ReactNode; darkText?: boolean; left?: number}> = ({
  children,
  darkText = true,
  left = 760,
}) => (
  <div
    style={{
      position: 'absolute',
      left,
      top: 34,
      zIndex: 190,
      color: darkText ? C.blue : '#8FB8F3',
      fontFamily: FONT,
      fontSize: 27,
      fontWeight: 780,
      letterSpacing: 0.5,
    }}
  >
    {children}
  </div>
);

const CaptionBar: React.FC<{
  index: string;
  children: ReactNode;
  left?: number;
  right?: number;
}> = ({index, children, left = 52, right = 820}) => (
  <div
    style={{
      position: 'absolute',
      left,
      right,
      bottom: 26,
      zIndex: 200,
      display: 'flex',
      alignItems: 'center',
      gap: 18,
      minHeight: 78,
      boxSizing: 'border-box',
      padding: '12px 22px 14px',
      borderRadius: 11,
      background: 'rgba(13,27,46,.94)',
      color: C.surface,
      boxShadow: '0 12px 34px rgba(13,27,46,.2)',
      fontFamily: FONT,
    }}
  >
    <span style={{color: '#75AFFF', fontSize: 23, fontWeight: 820}}>{index}</span>
    <strong style={{fontSize: 39, lineHeight: 1.08, letterSpacing: -0.8}}>{children}</strong>
  </div>
);

const Cursor: React.FC<{x: number; y: number; pressed?: number}> = ({x, y, pressed = 0}) => (
  <div
    style={{
      position: 'absolute',
      left: x,
      top: y,
      zIndex: 180,
      width: 46,
      height: 60,
      transform: `translate(-7px, -5px) scale(${1 - pressed * 0.09})`,
      transformOrigin: '8px 7px',
      filter: 'drop-shadow(0 4px 5px rgba(13,27,46,.32))',
    }}
  >
    <svg width="46" height="60" viewBox="0 0 46 60" aria-hidden="true">
      <path
        d="M5 3 L5 44 L15 35 L24 55 L33 51 L24 31 L39 30 Z"
        fill="#FFFFFF"
        stroke={C.navy}
        strokeWidth="4"
        strokeLinejoin="round"
      />
    </svg>
  </div>
);

const SCALE_X = WIDTH / workbenchLayout.capture.width;
const SCALE_Y = HEIGHT / workbenchLayout.capture.height;
const scaleRect = (rect: {x: number; y: number; width: number; height: number; radius: number}) => ({
  x: rect.x * SCALE_X,
  y: rect.y * SCALE_Y,
  width: rect.width * SCALE_X,
  height: rect.height * SCALE_Y,
  radius: rect.radius * Math.min(SCALE_X, SCALE_Y),
});
const GOAL = scaleRect(workbenchLayout.regions.goalInput);
const SEND = scaleRect(workbenchLayout.regions.sendButton);
const AGENT = scaleRect(workbenchLayout.regions.agentPanel);
const CENTER_X = scaleRect(workbenchLayout.regions.navigation).width;
const CENTER_WIDTH = AGENT.x - CENTER_X;

const Workbench: React.FC<{opacity?: number}> = ({opacity = 1}) => (
  <Img
    src={staticFile('textures/workbench-goal.jpg')}
    style={{position: 'absolute', inset: 0, width: WIDTH, height: HEIGHT, objectFit: 'fill', opacity}}
  />
);

const WorkbenchCamera: React.FC<{
  scale?: number;
  dim?: number;
  children?: ReactNode;
}> = ({scale = 1, dim = 0, children}) => (
  <div style={{position: 'absolute', inset: 0, overflow: 'hidden', background: C.surface}}>
    <div
      style={{
        position: 'absolute',
        inset: 0,
        transformOrigin: '100% 50%',
        transform: `scale(${scale})`,
      }}
    >
      <Workbench />
      {children}
      {dim > 0 ? <div style={{position: 'absolute', inset: 0, background: `rgba(13,27,46,${dim})`}} /> : null}
    </div>
  </div>
);

const SmallButton: React.FC<{
  children: ReactNode;
  primary?: boolean;
  active?: boolean;
  style?: CSSProperties;
}> = ({children, primary = false, active = false, style}) => (
  <div
    style={{
      display: 'inline-flex',
      alignItems: 'center',
      justifyContent: 'center',
      minHeight: 38,
      padding: '0 13px',
      borderRadius: 6,
      border: `1px solid ${primary || active ? C.blue : C.lineStrong}`,
      background: primary ? C.blue : active ? C.blueSoft : C.surface,
      color: primary ? C.surface : active ? C.blue : '#526B86',
      fontSize: 18,
      fontWeight: 700,
      ...style,
    }}
  >
    {children}
  </div>
);

const AgentPanel: React.FC<{
  children: ReactNode;
  composerText?: string;
}> = ({children, composerText = '说说你想弄清什么，也可以先添加资料'}) => (
  <div
    style={{
      position: 'absolute',
      left: AGENT.x,
      top: 0,
      width: AGENT.width,
      height: HEIGHT,
      boxSizing: 'border-box',
      borderLeft: `1px solid ${C.line}`,
      background: C.surface,
      color: C.ink,
      fontFamily: FONT,
    }}
  >
    <div
      style={{
        height: 72,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '0 27px',
        borderBottom: `1px solid ${C.line}`,
      }}
    >
      <div>
        <strong style={{display: 'block', fontSize: 24}}>数契 Agent</strong>
        <span style={{color: C.muted, fontSize: 14}}>一起把问题弄清楚</span>
      </div>
      <span style={{color: C.muted, fontSize: 16}}>展开对话</span>
    </div>
    <div
      style={{
        position: 'absolute',
        left: 0,
        right: 0,
        top: 72,
        bottom: 154,
        overflow: 'hidden',
        padding: '23px 28px 14px',
        boxSizing: 'border-box',
      }}
    >
      {children}
    </div>
    <div
      style={{
        position: 'absolute',
        left: 0,
        right: 0,
        bottom: 0,
        height: 154,
        boxSizing: 'border-box',
        borderTop: `1px solid ${C.line}`,
        padding: '14px 25px',
        background: C.surface,
      }}
    >
      <div
        style={{
          height: 72,
          border: `1px solid ${C.lineStrong}`,
          borderRadius: 8,
          padding: '11px 13px',
          boxSizing: 'border-box',
          color: C.quiet,
          fontSize: 17,
        }}
      >
        {composerText}
      </div>
      <div style={{display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginTop: 8}}>
        <span style={{color: C.muted, fontSize: 15}}>资料 · 3</span>
        <SmallButton primary>发送</SmallButton>
      </div>
    </div>
  </div>
);

const StepStrip: React.FC<{active: number; frame: number}> = ({active, frame}) => (
  <div style={{display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12}}>
    {['明确目标', '理解资料', '澄清口径', '整理成果'].map((label, index) => {
      const reached = index <= active;
      const current = index === active;
      const reveal = tween(frame, index * 4, index * 4 + 7);
      return (
        <div
          key={label}
          style={{
            minHeight: 70,
            display: 'flex',
            alignItems: 'center',
            gap: 12,
            padding: '0 16px',
            boxSizing: 'border-box',
            borderTop: `4px solid ${current ? C.blue : reached ? C.green : C.line}`,
            background: current ? C.blueSoft : '#F9FAFC',
            opacity: reveal,
          }}
        >
          <span
            style={{
              display: 'grid',
              placeItems: 'center',
              width: 32,
              height: 32,
              flex: '0 0 auto',
              borderRadius: '50%',
              background: current ? C.blue : reached ? C.green : C.line,
              color: C.surface,
              fontSize: 19,
              fontWeight: 850,
            }}
          >
            {reached && !current ? '✓' : index + 1}
          </span>
          <strong style={{fontSize: 25, color: current ? C.blue : C.ink}}>{label}</strong>
        </div>
      );
    })}
  </div>
);

const SendAndUnderstandScene: React.FC = () => {
  const frame = useCurrentFrame();
  const clickAt = 30;
  const pressed = pulse(frame, clickAt, 4);
  const cursor = pathPoint(frame, [
    {frame: 0, x: GOAL.x + GOAL.width * 0.35, y: GOAL.y + GOAL.height * 0.55},
    {frame: 24, x: SEND.x + SEND.width * 0.55, y: SEND.y + SEND.height * 0.55},
    {frame: 105, x: SEND.x + SEND.width * 0.55, y: SEND.y + SEND.height * 0.55},
  ]);
  const zoom =
    frame < 45
      ? tween(frame, clickAt, 45, 1, 1.13)
      : frame < 63
        ? 1.13
        : tween(frame, 63, 90, 1.13, 1.04, easeInOut);
  const progressIn = tween(frame, 48, 62);
  const screenshotDim = tween(frame, 48, 62, 0, 0.22);
  const origin = `${SEND.x}px ${SEND.y}px`;
  return (
    <AbsoluteFill style={{background: C.surface, fontFamily: FONT, color: C.ink, overflow: 'hidden'}}>
      <div style={{position: 'absolute', inset: 0, transformOrigin: origin, transform: `scale(${zoom})`}}>
        <Workbench />
        <div
          style={{
            position: 'absolute',
            left: SEND.x,
            top: SEND.y,
            width: SEND.width,
            height: SEND.height,
            borderRadius: SEND.radius,
            boxSizing: 'border-box',
            border: `3px solid ${C.blue}`,
            background: `rgba(22,116,243,${0.08 + pressed * 0.28})`,
            transform: `scale(${1 - pressed * 0.08})`,
          }}
        />
        <Cursor x={cursor.x} y={cursor.y} pressed={pressed} />
      </div>
      <div style={{position: 'absolute', inset: 0, background: `rgba(13,27,46,${screenshotDim})`}} />
      <div
        style={{
          position: 'absolute',
          left: 305,
          top: 155,
          width: 820,
          padding: '28px 31px 32px',
          boxSizing: 'border-box',
          border: `1px solid ${C.lineStrong}`,
          borderRadius: 14,
          background: 'rgba(255,255,255,.98)',
          boxShadow: '0 30px 80px rgba(13,27,46,.22)',
          opacity: progressIn,
          transform: `translateY(${(1 - progressIn) * 18}px)`,
        }}
      >
        <small style={{color: C.blue, fontSize: 23, fontWeight: 820}}>当前进展</small>
        <h2 style={{margin: '8px 0 20px', fontSize: 40, letterSpacing: -1.1}}>正在理解 3 份资料</h2>
        <StepStrip active={1} frame={Math.max(0, frame - 52)} />
      </div>
      <Kicker>一次真实推进</Kicker>
      <CaptionBar index="05" right={620}>按下发送，开始理解资料</CaptionBar>
    </AbsoluteFill>
  );
};

const QUESTIONS = [
  {
    question: '待支付订单算不算？',
    why: '会影响 199 元是否进入汇总。',
    answer: '只统计已支付订单',
    source: '业务负责人',
    basis: '本次汇总只看已经完成支付的订单。',
  },
  {
    question: '退款从哪里扣？',
    why: '会影响华东是否减去 50 元。',
    answer: '从原订单地区扣除',
    source: '业务负责人',
    basis: '退款沿用原订单的客户地区。',
  },
  {
    question: '按哪个时间归属？',
    why: '会影响跨日、跨月时落在哪个期间。',
    answer: '按支付时间归属',
    source: '数据负责人',
    basis: '本次口径以 paid_at 的北京时间为准。',
  },
] as const;

const QuestionPreview: React.FC<{frame: number; index: number}> = ({frame, index}) => {
  const reveal = tween(frame, 0, 8);
  const item = QUESTIONS[index];
  return (
    <div
      style={{
        marginTop: 12,
        padding: '16px 17px',
        border: `1px solid ${C.lineStrong}`,
        borderRadius: 8,
        background: C.surface,
        opacity: reveal,
        transform: `translateY(${(1 - reveal) * 10}px)`,
      }}
    >
      <strong style={{fontSize: 23, lineHeight: 1.45}}>{index + 1}. {item.question}</strong>
      <p style={{margin: '8px 0 12px', color: C.muted, fontSize: 17, lineHeight: 1.55}}>
        为什么需要确认：{item.why}
      </p>
      <div style={{display: 'flex', flexWrap: 'wrap', gap: 7}}>
        <SmallButton>{item.answer}</SmallButton>
        <SmallButton>暂时不知道</SmallButton>
      </div>
      <div style={{marginTop: 13, color: C.blue, fontSize: 16, fontWeight: 750}}>
        {index + 1} / 3 · 回答卡片中的真实问题结构
      </div>
    </div>
  );
};

const AgentQuestionsScene: React.FC = () => {
  const frame = useCurrentFrame();
  const starts = [10, 58, 106];
  const index = frame < starts[1] ? 0 : frame < starts[2] ? 1 : 2;
  return (
    <AbsoluteFill style={{background: C.surface, fontFamily: FONT, color: C.ink}}>
      <WorkbenchCamera scale={1.16}>
        <AgentPanel>
          <div style={{marginBottom: 15}}>
            <div style={{display: 'flex', justifyContent: 'space-between', color: C.muted, fontSize: 16}}>
              <strong style={{color: C.ink}}>数契 Agent</strong><span>刚刚</span>
            </div>
            <p style={{margin: '8px 0 0', fontSize: 20, lineHeight: 1.6}}>
              我找到了 3 个会改变结果的问题。先确认这些规则，我不会替你猜。
            </p>
          </div>
          <div style={{padding: '14px 15px', border: `1px solid #D6E3F3`, borderRadius: 9, background: '#F8FBFF'}}>
            <span style={{color: C.blue, fontSize: 15, fontWeight: 800}}>建议下一步</span>
            <strong style={{display: 'block', marginTop: 5, fontSize: 19}}>回答 3 个会改变结果的问题</strong>
            <div style={{display: 'flex', gap: 7, marginTop: 10}}>
              <SmallButton primary>填写回答卡片</SmallButton>
              <SmallButton>为什么需要确认</SmallButton>
            </div>
          </div>
          <div style={{marginTop: 16, padding: '15px', border: `1px solid #D7E3F4`, borderRadius: 8, background: '#FAFCFF'}}>
            <strong style={{color: '#2F4D6D', fontSize: 20}}>需要你确认的业务口径</strong>
            <p style={{margin: '6px 0 0', color: '#7589A0', fontSize: 16, lineHeight: 1.5}}>
              请检查回答、来源和仍未知的事项；只有确认后才会采用。
            </p>
            <QuestionPreview frame={frame - starts[index]} index={index} />
          </div>
        </AgentPanel>
      </WorkbenchCamera>
      <Kicker>Agent 把缺口问出来</Kicker>
      <CaptionBar index="06">会改变结果的问题，先问清楚</CaptionBar>
    </AbsoluteFill>
  );
};

const AnswerFormCard: React.FC<{frame: number; index: number}> = ({frame, index}) => {
  const item = QUESTIONS[index];
  const selected = frame >= 30;
  return (
    <div>
      <div style={{marginBottom: 12}}>
        <strong style={{color: '#2F4D6D', fontSize: 20}}>需要你确认的业务口径</strong>
        <p style={{margin: '5px 0 0', color: '#7589A0', fontSize: 15}}>
          Agent 已把对话整理成卡片。请检查回答、来源和依据。
        </p>
      </div>
      <div style={{paddingTop: 10, borderTop: `1px solid ${C.line}`}}>
        <strong style={{color: '#41698F', fontSize: 17}}>3 个问题 · 等待填写</strong>
        <div style={{marginTop: 11, padding: '14px', border: `1px solid ${C.lineStrong}`, borderRadius: 8}}>
          <strong style={{display: 'block', fontSize: 22, lineHeight: 1.4}}>{index + 1}. {item.question}</strong>
          <p style={{margin: '6px 0 10px', color: C.muted, fontSize: 15}}>为什么需要确认：{item.why}</p>
          <span style={{color: C.muted, fontSize: 14}}>可以直接选择</span>
          <div style={{display: 'flex', gap: 7, marginTop: 5}}>
            <SmallButton active={selected}>{item.answer}</SmallButton>
          </div>
          <div style={{marginTop: 10, fontSize: 15, fontWeight: 700, color: '#485F78'}}>你现在能确认吗？</div>
          <div style={{display: 'flex', gap: 7, marginTop: 6}}>
            <SmallButton active={selected}>我可以确认</SmallButton>
            <SmallButton>暂时不知道</SmallButton>
          </div>
          <label style={{display: 'block', marginTop: 10, fontSize: 15, fontWeight: 700}}>
            回答
            <div style={{marginTop: 5, minHeight: 44, padding: '10px', border: `1px solid ${C.lineStrong}`, borderRadius: 5, fontWeight: 500}}>
              {selected ? item.answer : ''}
            </div>
          </label>
          <div style={{marginTop: 10, padding: '9px 11px', border: `1px solid #E0E7EF`, borderRadius: 7}}>
            <strong style={{fontSize: 15, color: '#4D6680'}}>回答来源与依据（保存必填）</strong>
            <div style={{display: 'grid', gridTemplateColumns: '.72fr 1.28fr', gap: 7, marginTop: 7}}>
              <div style={{padding: '8px', border: `1px solid ${C.line}`, borderRadius: 5, fontSize: 14}}>
                {selected ? item.source : '回答来源人或角色'}
              </div>
              <div style={{padding: '8px', border: `1px solid ${C.line}`, borderRadius: 5, fontSize: 14}}>
                {selected ? item.basis : '回答依据'}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

const SaveReview: React.FC<{confirmed: boolean}> = ({confirmed}) => (
  <div>
    <strong style={{color: '#2F4D6D', fontSize: 21}}>需要你确认的业务口径</strong>
    <p style={{margin: '7px 0 12px', color: C.muted, fontSize: 16}}>
      3 个问题已经填写完整，来源与依据也已补齐。
    </p>
    <div style={{borderTop: `1px solid ${C.line}`}}>
      {QUESTIONS.map((item, index) => (
        <div
          key={item.question}
          style={{
            display: 'grid',
            gridTemplateColumns: '38px 1fr',
            gap: 10,
            padding: '13px 0',
            borderBottom: `1px solid ${C.line}`,
          }}
        >
          <span style={{display: 'grid', placeItems: 'center', width: 30, height: 30, borderRadius: '50%', background: C.blue, color: C.surface, fontWeight: 850}}>✓</span>
          <div>
            <strong style={{display: 'block', fontSize: 18}}>{item.answer}</strong>
            <span style={{color: C.muted, fontSize: 14}}>来源：{item.source} · 依据已填写</span>
          </div>
        </div>
      ))}
    </div>
    {!confirmed ? (
      <>
        <SmallButton primary style={{marginTop: 15, minWidth: 190}}>仅保存，稍后继续</SmallButton>
        <p style={{margin: '7px 0 0', color: C.muted, fontSize: 14}}>保存不会调用模型，也不会自动采用业务结论。</p>
      </>
    ) : (
      <>
        <div style={{marginTop: 13, padding: '10px 12px', background: C.greenSoft, color: C.green, fontSize: 16, fontWeight: 750}}>
          回答已保存 · 尚未批准
        </div>
        <SmallButton primary style={{marginTop: 12, minWidth: 190}}>确认并继续</SmallButton>
      </>
    )}
  </div>
);

const HumanAnswersScene: React.FC = () => {
  const frame = useCurrentFrame();
  const index = Math.min(2, Math.floor(frame / 45));
  const phaseFrame = frame - index * 45;
  const savePhase = frame >= 135 && frame < 150;
  const confirmPhase = frame >= 150;
  const answerKicker =
    frame < 135
      ? '人的决定 · 逐项回答'
      : savePhase
        ? '人的决定 · 先保存'
        : frame < 171
          ? '已保存 · 尚未批准'
          : '人的决定 · 已确认采用';
  const clicks = [30, 75, 120, 140, 165];
  const pressed = Math.max(...clicks.map((at) => pulse(frame, at, 4)));
  const cursor = pathPoint(frame, [
    {frame: 0, x: AGENT.x + 160, y: 420},
    {frame: 24, x: AGENT.x + 90, y: 330},
    {frame: 36, x: AGENT.x + 90, y: 330},
    {frame: 69, x: AGENT.x + 90, y: 330},
    {frame: 81, x: AGENT.x + 90, y: 330},
    {frame: 114, x: AGENT.x + 90, y: 330},
    {frame: 126, x: AGENT.x + 90, y: 330},
    {frame: 136, x: AGENT.x + 145, y: 400},
    {frame: 145, x: AGENT.x + 145, y: 400},
    {frame: 158, x: AGENT.x + 145, y: 462},
    {frame: 180, x: AGENT.x + 145, y: 462},
  ]);
  return (
    <AbsoluteFill style={{background: C.surface, fontFamily: FONT, color: C.ink}}>
      <WorkbenchCamera scale={1.16}>
        <AgentPanel>
          {frame < 135 ? <AnswerFormCard frame={phaseFrame} index={index} /> : <SaveReview confirmed={confirmPhase} />}
          {frame >= 171 ? (
            <div style={{position: 'absolute', left: 28, right: 28, bottom: 18, padding: '10px 12px', background: C.greenSoft, color: C.green, fontSize: 16, fontWeight: 760}}>
              整份回答已确认 · 后续分析已经开始
            </div>
          ) : null}
        </AgentPanel>
        <Cursor x={cursor.x} y={cursor.y} pressed={pressed} />
      </WorkbenchCamera>
      <Kicker>{answerKicker}</Kicker>
      <CaptionBar index="07">回答、来源和依据，都由人确认</CaptionBar>
    </AbsoluteFill>
  );
};

const WorkbenchProgress: React.FC<{active: number}> = ({active}) => (
  <div
    style={{
      position: 'absolute',
      left: CENTER_X,
      top: 0,
      width: CENTER_WIDTH,
      height: 356,
      boxSizing: 'border-box',
      padding: '42px 32px 0',
      background: '#FBFCFE',
      borderRight: `1px solid ${C.line}`,
      borderBottom: `1px solid ${C.line}`,
      color: C.ink,
    }}
  >
    <span style={{color: C.blue, fontSize: 18, fontWeight: 780}}>当前进展</span>
    <h1 style={{margin: '8px 0 18px', fontSize: 38, letterSpacing: -1.2}}>按地区统计订单金额</h1>
    <StepStrip active={active} frame={30} />
    <div style={{display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', marginTop: 17, border: `1px solid ${C.line}`, borderRadius: 8, overflow: 'hidden'}}>
      {[
        ['正在做什么', active === 3 ? '候选成果已经更新' : '正在澄清业务口径'],
        ['需要你做什么', active === 3 ? '核对本轮结果' : '回答关键问题'],
        ['已经得到什么', active === 3 ? '1 个候选字段 · 1 个关系' : '3 个待确认问题'],
      ].map(([label, value], index) => (
        <div key={label} style={{padding: '13px 15px', background: index === 1 ? C.blueSoft : C.surface, borderLeft: index ? `1px solid ${C.line}` : 'none'}}>
          <span style={{display: 'block', color: C.muted, fontSize: 14}}>{label}</span>
          <strong style={{display: 'block', marginTop: 5, color: index === 1 ? C.blue : C.ink, fontSize: 17}}>{value}</strong>
        </div>
      ))}
    </div>
    <div style={{display: 'flex', gap: 28, marginTop: 18, fontSize: 17, fontWeight: 700}}>
      <span style={{color: active === 3 ? C.muted : C.blue}}>当前进展</span>
      <span style={{color: active === 3 ? C.blue : C.muted}}>关系与字段</span>
      <span style={{color: C.muted}}>过程记录</span>
    </div>
  </div>
);

const ResultAgentPanel: React.FC<{confirmed?: boolean}> = ({confirmed = true}) => (
  <AgentPanel composerText="继续补充口径，也可以查看本轮变化">
    <div style={{fontSize: 17, color: C.muted}}>当前已明确目标</div>
    <p style={{margin: '7px 0 18px', fontSize: 19, lineHeight: 1.55}}>按地区统计订单金额，并把退款和缺失金额的处理规则写清楚。</p>
    <div style={{padding: '13px 0', borderTop: `1px solid ${C.line}`}}>
      <strong style={{fontSize: 19}}>数契 Agent</strong>
      <p style={{margin: '7px 0 0', fontSize: 18, lineHeight: 1.55}}>
        已应用这次确认的业务回答，候选方案已经更新；当前没有待回答的业务口径。
      </p>
    </div>
    <div style={{marginTop: 12, padding: '14px', border: `1px solid #D6E3F3`, borderRadius: 9, background: '#F8FBFF'}}>
      <span style={{color: C.blue, fontSize: 15, fontWeight: 780}}>建议下一步</span>
      <strong style={{display: 'block', marginTop: 6, fontSize: 19}}>核对候选成果和仍未知的事项</strong>
      <div style={{display: 'flex', gap: 7, marginTop: 11}}>
        <SmallButton primary>查看本轮变化</SmallButton>
        <SmallButton>继续补充口径</SmallButton>
      </div>
    </div>
    <div style={{marginTop: 16, padding: '13px 15px', border: `1px solid #D7E3F4`, borderRadius: 8, background: '#FAFCFF'}}>
      <strong style={{fontSize: 18, color: '#2F4D6D'}}>{confirmed ? '业务回答已确认 · 展开查看' : '需要你确认的业务口径'}</strong>
    </div>
  </AgentPanel>
);

const DefinitionRows: React.FC<{frame: number}> = ({frame}) => {
  const rows = [
    ['业务含义', '按客户地区汇总已支付金额，再扣除同一地区的退款金额'],
    ['值类型', '人民币金额'],
    ['业务粒度', '客户地区 × 统计期间'],
    ['业务规则', '只统计已支付；退款从原订单地区扣除'],
    ['时间口径', '按支付时间归属'],
  ];
  return (
    <div style={{display: 'grid', gap: 0, borderTop: `1px solid ${C.line}`}}>
      {rows.map(([label, value], index) => {
        const reveal = tween(frame, 32 + index * 16, 42 + index * 16);
        return (
          <div key={label} style={{display: 'grid', gridTemplateColumns: '150px 1fr', padding: '11px 0', borderBottom: `1px solid ${C.line}`, opacity: reveal, clipPath: `inset(0 ${(1 - reveal) * 100}% 0 0)`}}>
            <strong style={{color: C.muted, fontSize: 16}}>{label}</strong>
            <span style={{fontSize: 18, fontWeight: 650}}>{value}</span>
          </div>
        );
      })}
    </div>
  );
};

const CandidateCenter: React.FC<{frame: number}> = ({frame}) => (
  <div
    style={{
      position: 'absolute',
      left: CENTER_X,
      top: 356,
      width: CENTER_WIDTH,
      bottom: 0,
      boxSizing: 'border-box',
      padding: '28px 32px 112px',
      background: '#F8FAFC',
      borderRight: `1px solid ${C.line}`,
      color: C.ink,
      overflow: 'hidden',
    }}
  >
    <div style={{display: 'flex', alignItems: 'center', justifyContent: 'space-between'}}>
      <div>
        <span style={{color: C.blue, fontSize: 16, fontWeight: 800}}>候选成果 · 等待核对</span>
        <h2 style={{margin: '7px 0 0', fontSize: 31}}>关系与字段</h2>
      </div>
      <span style={{padding: '7px 10px', background: C.amberSoft, color: C.amber, fontSize: 14, fontWeight: 780}}>候选，尚未批准</span>
    </div>
    <div style={{marginTop: 18, padding: '21px 24px', border: `1px solid ${C.lineStrong}`, borderRadius: 9, background: C.surface, opacity: tween(frame, 0, 12), transform: `translateY(${(1 - tween(frame, 0, 12)) * 14}px)`}}>
      <div style={{display: 'flex', alignItems: 'baseline', justifyContent: 'space-between'}}>
        <div>
          <span style={{color: C.muted, fontSize: 15}}>字段名称</span>
          <h3 style={{margin: '5px 0 0', fontSize: 32}}>地区净订单金额</h3>
        </div>
        <span style={{color: C.green, fontSize: 15, fontWeight: 750}}>证据状态：候选</span>
      </div>
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: '150px 1fr',
          marginTop: 13,
          padding: '10px 12px',
          borderLeft: `5px solid ${C.blue}`,
          background: C.blueSoft,
          opacity: tween(frame, 18, 28),
          clipPath: `inset(0 ${(1 - tween(frame, 18, 28)) * 100}% 0 0)`,
        }}
      >
        <strong style={{color: C.blue, fontSize: 16}}>关系候选</strong>
        <span style={{fontSize: 17, fontWeight: 680}}>orders.customer_id → customers.customer_id · 多对一</span>
      </div>
      <DefinitionRows frame={frame} />
      <div style={{marginTop: 10, color: C.muted, fontSize: 15, opacity: tween(frame, 104, 114)}}>
        资料来源：orders.csv · customers.csv · notes.md
      </div>
      <div style={{marginTop: 13, padding: '11px 13px', borderLeft: `5px solid ${C.amber}`, background: C.amberSoft, opacity: tween(frame, 116, 128)}}>
        <strong style={{fontSize: 16, color: C.amber}}>仍未解决</strong>
        <span style={{marginLeft: 12, fontSize: 17}}>跨月退款边界仍待补充</span>
      </div>
    </div>
  </div>
);

const ChangesCenter: React.FC<{frame: number}> = ({frame}) => (
  <div
    style={{
      position: 'absolute',
      left: CENTER_X,
      top: 356,
      width: CENTER_WIDTH,
      bottom: 0,
      boxSizing: 'border-box',
      padding: '28px 32px 112px',
      background: '#F8FAFC',
      borderRight: `1px solid ${C.line}`,
      color: C.ink,
      overflow: 'hidden',
    }}
  >
    <span style={{color: C.blue, fontSize: 16, fontWeight: 800}}>本轮更新</span>
    <h2 style={{margin: '7px 0 4px', fontSize: 31}}>候选成果已经更新</h2>
    <p style={{margin: 0, color: C.muted, fontSize: 16}}>这里展示已采用的业务回答、它带来的变化和仍待确认的事项。</p>
    <div style={{marginTop: 15, padding: '12px 15px', border: `1px solid ${C.line}`, borderRadius: 8, background: C.surface}}>
      <strong style={{fontSize: 17}}>查看本轮采用的回答 · 3 个问题</strong>
    </div>
    <h3 style={{margin: '18px 0 9px', fontSize: 20}}>这次回答带来的变化</h3>
    {[
      ['人的回答', '纳入、退款和时间规则已经确认', C.blue, C.blueSoft],
      ['候选变化', '地区净订单金额 · 新增', C.green, C.greenSoft],
      ['仍未解决', '跨月退款边界还要补充', C.amber, C.amberSoft],
    ].map(([label, value, color, background], index) => {
      const reveal = tween(frame, 7 + index * 22, 17 + index * 22);
      return (
        <div
          key={label}
          style={{
            minHeight: 88,
            display: 'grid',
            gridTemplateColumns: '145px 1fr',
            alignItems: 'center',
            padding: '0 18px',
            boxSizing: 'border-box',
            borderTop: `1px solid ${C.line}`,
            borderLeft: `6px solid ${color}`,
            background,
            opacity: reveal,
            clipPath: `inset(0 ${(1 - reveal) * 100}% 0 0)`,
          }}
        >
          <strong style={{color, fontSize: 17}}>{label}</strong>
          <span style={{fontSize: 20, fontWeight: 700}}>{value}</span>
        </div>
      );
    })}
    <div style={{marginTop: 14, color: C.muted, fontSize: 15, opacity: tween(frame, 72, 84)}}>
      资料来源：orders.csv · customers.csv · notes.md
    </div>
  </div>
);

const CandidateDefinitionScene: React.FC = () => {
  const frame = useCurrentFrame();
  return (
    <AbsoluteFill style={{background: C.surface, fontFamily: FONT, color: C.ink}}>
      <WorkbenchCamera>
        <WorkbenchProgress active={3} />
        <CandidateCenter frame={frame} />
        <ResultAgentPanel />
      </WorkbenchCamera>
      <Kicker>真实工作区 · 候选定义</Kicker>
      <CaptionBar index="08">回答进入候选字段，不是最终批准</CaptionBar>
    </AbsoluteFill>
  );
};

const RoundChangesScene: React.FC = () => {
  const frame = useCurrentFrame();
  const clickAt = 15;
  const switchIn = tween(frame, 22, 31);
  const cursor = pathPoint(frame, [
    {frame: 0, x: AGENT.x + 390, y: 334},
    {frame: 12, x: AGENT.x + 126, y: 362},
    {frame: 120, x: AGENT.x + 126, y: 362},
  ]);
  return (
    <AbsoluteFill style={{background: C.surface, fontFamily: FONT, color: C.ink}}>
      <WorkbenchCamera>
        <WorkbenchProgress active={3} />
        <div style={{opacity: 1 - switchIn}}><CandidateCenter frame={180} /></div>
        <div style={{opacity: switchIn}}><ChangesCenter frame={Math.max(0, frame - 22)} /></div>
        <ResultAgentPanel />
        <Cursor x={cursor.x} y={cursor.y} pressed={pulse(frame, clickAt, 4)} />
      </WorkbenchCamera>
      <Kicker>真实控件 · 查看本轮变化</Kicker>
      <CaptionBar index="09">人的回答、变化和未知，都能回看</CaptionBar>
    </AbsoluteFill>
  );
};

const PositioningScene: React.FC = () => {
  const frame = useCurrentFrame();
  const wash = tween(frame, 0, 14, 0, 0.92);
  const leftIn = tween(frame, 8, 24);
  const rightIn = tween(frame, 34, 50);
  const handoffIn = tween(frame, 90, 105);
  return (
    <AbsoluteFill style={{background: C.surface, fontFamily: FONT, color: C.ink, overflow: 'hidden'}}>
      <Workbench opacity={0.42} />
      <div style={{position: 'absolute', inset: '0 50% 0 0', background: `rgba(232,237,243,${wash})`}} />
      <div style={{position: 'absolute', inset: '0 0 0 50%', background: `rgba(244,241,234,${wash})`}} />
      <div style={{position: 'absolute', left: '50%', top: 0, bottom: 0, width: 2, background: C.lineStrong, opacity: wash}} />
      <Kicker>分工不同 · 可以接力</Kicker>
      <div
        style={{
          position: 'absolute',
          left: 120,
          top: 170,
          width: 730,
          opacity: leftIn,
          clipPath: `inset(0 0 0 ${(1 - leftIn) * 100}%)`,
        }}
      >
        <span style={{color: C.muted, fontSize: 28, fontWeight: 760}}>通用执行型 Agent</span>
        <h2 style={{margin: '20px 0 0', fontSize: 58, lineHeight: 1.16, letterSpacing: -2}}>目标清楚后，<br />把任务做出来</h2>
        <p style={{margin: '30px 0 0', color: C.muted, fontSize: 31, lineHeight: 1.55}}>代码 · 查询 · 文档 · 工程交付</p>
      </div>
      <div
        style={{
          position: 'absolute',
          left: 1080,
          top: 170,
          width: 720,
          opacity: rightIn,
          clipPath: `inset(0 ${(1 - rightIn) * 100}% 0 0)`,
        }}
      >
        <span style={{color: C.blue, fontSize: 28, fontWeight: 800}}>数契</span>
        <h2 style={{margin: '20px 0 0', fontSize: 58, lineHeight: 1.16, letterSpacing: -2}}>目标还没说清时，<br />先把定义问明白</h2>
        <p style={{margin: '30px 0 0', color: C.muted, fontSize: 31, lineHeight: 1.55}}>证据 · 人的确认 · 变化 · 仍未知</p>
      </div>
      <div
        style={{
          position: 'absolute',
          left: 220,
          right: 220,
          bottom: 150,
          minHeight: 120,
          display: 'grid',
          placeItems: 'center',
          padding: '0 32px',
          boxSizing: 'border-box',
          background: C.navy,
          color: C.surface,
          opacity: handoffIn,
          clipPath: `inset(0 ${(1 - handoffIn) * 50}% 0 ${(1 - handoffIn) * 50}%)`,
        }}
      >
        <strong style={{fontSize: 42}}>先用数契说清楚 → 再交给 Codex 或数据团队执行</strong>
        <small style={{position: 'absolute', bottom: 10, color: '#9FB0C5', fontSize: 20}}>这是默认注重点，不是绝对能力边界</small>
      </div>
    </AbsoluteFill>
  );
};

const BrandCloseScene: React.FC = () => {
  const frame = useCurrentFrame();
  const settle = tween(frame, 0, 15, 0, 1, easeInOut);
  const copyIn = tween(frame, 10, 26);
  return (
    <AbsoluteFill style={{...dark, fontFamily: FONT, color: C.surface, overflow: 'hidden'}}>
      <div
        style={{
          position: 'absolute',
          left: interpolate(settle, [0, 1], [0, 1300]),
          top: interpolate(settle, [0, 1], [0, 120]),
          width: interpolate(settle, [0, 1], [WIDTH, 430]),
          height: interpolate(settle, [0, 1], [HEIGHT, 242]),
          border: '1px solid rgba(255,255,255,.18)',
          borderRadius: interpolate(settle, [0, 1], [0, 14]),
          overflow: 'hidden',
          opacity: 1 - settle * 0.3,
          boxShadow: '0 24px 70px rgba(0,0,0,.28)',
        }}
      >
        <Img src={staticFile('textures/workbench-goal.jpg')} style={{width: '100%', height: '100%', objectFit: 'cover'}} />
      </div>
      <div style={{position: 'absolute', left: 145, top: 185, width: 1080, opacity: copyIn, transform: `translateY(${(1 - copyIn) * 22}px)`}}>
        <div style={{display: 'flex', alignItems: 'center', gap: 20}}>
          <Img src={staticFile('contextox-mark.png')} style={{width: 92, height: 92}} />
          <span style={{color: '#8FB8F3', fontSize: 30, fontWeight: 820}}>数契 ContextOx · Demo 1.0.0</span>
        </div>
        <h1 style={{margin: '32px 0 0', fontSize: 82, lineHeight: 1.12, letterSpacing: -4}}>把表里的<br /><span style={{color: '#7EB2FA'}}>业务意思说清楚</span></h1>
        <p style={{margin: '30px 0 0', color: '#C5D2E2', fontSize: 31}}>带一个总对不上的业务口径，和数契一起把它说明白。</p>
      </div>
      <div
        style={{
          position: 'absolute',
          left: 145,
          right: 145,
          bottom: 72,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          color: '#AFC0D3',
          fontSize: 27,
          opacity: tween(frame, 24, 30),
        }}
      >
        <span>公开合成 Demo · 候选结果由人核对</span>
        <strong style={{color: C.surface}}>联系 @archerthegoat · github.com/archerthegoat/contextox-agent</strong>
      </div>
    </AbsoluteFill>
  );
};

function musicVolume(frame: number): number {
  return interpolate(
    frame,
    [0, 1290, 1349],
    [0.72, 0.72, 0],
    {...clamp, easing: easeInOut},
  );
}

const EndFade: React.FC = () => {
  const frame = useCurrentFrame();
  return (
    <AbsoluteFill
      style={{
        zIndex: 999,
        background: '#05070A',
        opacity: tween(frame, 1320, 1349, 0, 1, easeInOut),
        pointerEvents: 'none',
      }}
    />
  );
};

const V4FullAudio: React.FC = () => (
  <>
    <Audio
      src={staticFile('audio/v4/dirty-thinkin.mp3')}
      trimBefore={165}
      volume={(frame) => musicVolume(frame)}
    />
    <Sequence from={7} durationInFrames={13}><Audio src={staticFile('audio/v4/paper-slice-quick.mp3')} volume={0.13} /></Sequence>
    {[76, 116, 156].map((from) => (
      <Sequence key={from} from={from} durationInFrames={14}><Audio src={staticFile('audio/v4/marker-pen-line.mp3')} volume={0.11} /></Sequence>
    ))}
    <Sequence from={227} durationInFrames={13}><Audio src={staticFile('audio/v4/paper-slice-quick.mp3')} volume={0.1} /></Sequence>
    {[
      {from: 390, volume: 0.2},
      {from: 660, volume: 0.13},
      {from: 705, volume: 0.13},
      {from: 750, volume: 0.13},
      {from: 770, volume: 0.13},
      {from: 1005, volume: 0.15},
    ].map(({from, volume}) => (
      <Sequence key={from} from={from} durationInFrames={8}><Audio src={staticFile('audio/v4/ui-click.wav')} volume={volume} /></Sequence>
    ))}
    <Sequence from={795} durationInFrames={18}><Audio src={staticFile('audio/v4/confirm-tick.wav')} volume={0.18} /></Sequence>
    <Sequence from={810} durationInFrames={24}><Audio src={staticFile('audio/v4/result-open.wav')} volume={0.15} /></Sequence>
  </>
);

export const ContextOxProductFilmV4A: React.FC = () => (
  <AbsoluteFill style={{background: C.navy, fontFamily: FONT}}>
    <Sequence from={0} durationInFrames={360}><ContextOxProductFilmV4HookAVisual /></Sequence>
    <Sequence from={360} durationInFrames={105}><SendAndUnderstandScene /></Sequence>
    <Sequence from={465} durationInFrames={165}><AgentQuestionsScene /></Sequence>
    <Sequence from={630} durationInFrames={180}><HumanAnswersScene /></Sequence>
    <Sequence from={810} durationInFrames={180}><CandidateDefinitionScene /></Sequence>
    <Sequence from={990} durationInFrames={120}><RoundChangesScene /></Sequence>
    <Sequence from={1110} durationInFrames={150}><PositioningScene /></Sequence>
    <Sequence from={1260} durationInFrames={90}><BrandCloseScene /></Sequence>
    <V4FullAudio />
    <EndFade />
  </AbsoluteFill>
);
