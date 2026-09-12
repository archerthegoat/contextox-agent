import React, {type CSSProperties, type ReactNode} from 'react';
import {
  AbsoluteFill,
  Easing,
  Img,
  interpolate,
  Sequence,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';

const colors = {
  paper: '#F5F7FA',
  surface: '#FFFFFF',
  ink: '#13243A',
  muted: '#607086',
  line: '#DCE3EB',
  blue: '#1674F3',
};

const fontFamily = '-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Hiragino Sans GB", sans-serif';

const gridBackground: CSSProperties = {
  backgroundColor: colors.paper,
  backgroundImage: `linear-gradient(${colors.line} 1px, transparent 1px), linear-gradient(90deg, ${colors.line} 1px, transparent 1px)`,
  backgroundSize: '60px 60px',
};

const Scene: React.FC<{children: ReactNode; label: string}> = ({children, label}) => {
  const frame = useCurrentFrame();
  const {durationInFrames} = useVideoConfig();
  return (
    <AbsoluteFill
      style={{
        ...gridBackground,
        color: colors.ink,
        fontFamily,
        opacity: interpolate(frame, [0, 12, durationInFrames - 12, durationInFrames], [0, 1, 1, 0], {
          extrapolateLeft: 'clamp',
          extrapolateRight: 'clamp',
          easing: Easing.bezier(0.16, 1, 0.3, 1),
        }),
      }}
    >
      <div style={{position: 'absolute', left: 120, top: 76, color: colors.blue, fontSize: 22, fontWeight: 750, letterSpacing: 1.5}}>{label}</div>
      <div style={{position: 'absolute', right: 120, bottom: 68, color: colors.muted, fontSize: 18}}>数契 ContextOx · Demo 1.0.0</div>
      {children}
    </AbsoluteFill>
  );
};

const Reveal: React.FC<{children: ReactNode; delay?: number; style?: CSSProperties}> = ({children, delay = 0, style}) => {
  const frame = useCurrentFrame();
  return (
    <div
      style={{
        opacity: interpolate(frame, [delay, delay + 20], [0, 1], {
          extrapolateLeft: 'clamp',
          extrapolateRight: 'clamp',
          easing: Easing.bezier(0.16, 1, 0.3, 1),
        }),
        translate: interpolate(frame, [delay, delay + 24], ['0px 24px', '0px 0px'], {
          extrapolateLeft: 'clamp',
          extrapolateRight: 'clamp',
          easing: Easing.bezier(0.16, 1, 0.3, 1),
        }),
        ...style,
      }}
    >
      {children}
    </div>
  );
};

const BigTitle: React.FC<{children: ReactNode}> = ({children}) => (
  <div style={{fontSize: 104, lineHeight: 1.06, letterSpacing: -6, fontWeight: 780}}>{children}</div>
);

const Card: React.FC<{label: string; title: string; body: string; delay: number}> = ({label, title, body, delay}) => (
  <Reveal delay={delay} style={{flex: 1, minHeight: 270, borderTop: `5px solid ${colors.blue}`, background: colors.surface, padding: 38, boxShadow: '0 22px 60px rgba(19,36,58,.08)'}}>
    <div style={{color: colors.blue, fontSize: 18, fontWeight: 750}}>{label}</div>
    <div style={{marginTop: 62, fontSize: 38, fontWeight: 740}}>{title}</div>
    <div style={{marginTop: 16, color: colors.muted, fontSize: 23, lineHeight: 1.55}}>{body}</div>
  </Reveal>
);

const Hook: React.FC = () => (
  <Scene label="CONTEXTOX · 公开合成演示">
    <div style={{position: 'absolute', left: 120, top: 190, right: 120}}>
      <Reveal delay={5}><Img src={staticFile('contextox-mark.png')} style={{width: 118, height: 118}} /></Reveal>
      <Reveal delay={18} style={{marginTop: 56}}><BigTitle>让每一张表，<br />都说清自己代表什么。</BigTitle></Reveal>
      <Reveal delay={46} style={{marginTop: 34, color: colors.muted, fontSize: 36}}>有证据、可确认的业务定义</Reveal>
    </div>
  </Scene>
);

const Problem: React.FC = () => (
  <Scene label="01 / 一个看似简单的需求">
    <div style={{position: 'absolute', left: 120, right: 120, top: 170}}>
      <Reveal delay={4}><BigTitle>“我想按地区统计订单金额。”</BigTitle></Reveal>
      <div style={{display: 'flex', gap: 28, marginTop: 72}}>
        <Card label="退款" title="应该扣除吗？" body="按发生地区、订单地区，还是单独列出？" delay={22} />
        <Card label="缺失" title="应该归零吗？" body="排除、归零与标记异常，会产生不同结果。" delay={35} />
        <Card label="时间" title="按什么时间？" body="下单、支付与退款日期，对归属并不相同。" delay={48} />
      </div>
    </div>
  </Scene>
);

const Method: React.FC = () => {
  const items = ['选择本轮资料', '定位证据缺口', '人确认业务事实', '形成候选定义'];
  return (
    <Scene label="02 / 数契的方法">
      <div style={{position: 'absolute', left: 120, right: 120, top: 180}}>
        <Reveal delay={3}><BigTitle>Agent 找证据，<br />人确认业务事实。</BigTitle></Reveal>
        <div style={{display: 'flex', alignItems: 'center', marginTop: 90}}>
          {items.map((item, index) => (
            <React.Fragment key={item}>
              <Reveal delay={30 + index * 16} style={{width: 340, minHeight: 130, display: 'grid', alignContent: 'center', border: `1px solid ${colors.line}`, background: colors.surface, padding: 28}}>
                <div style={{color: colors.blue, fontSize: 18, fontWeight: 750}}>0{index + 1}</div>
                <div style={{fontSize: 28, fontWeight: 720, marginTop: 10}}>{item}</div>
              </Reveal>
              {index < items.length - 1 ? <Reveal delay={40 + index * 16} style={{width: 90, textAlign: 'center', color: colors.blue, fontSize: 42}}>→</Reveal> : null}
            </React.Fragment>
          ))}
        </div>
      </div>
    </Scene>
  );
};

const Evidence: React.FC = () => {
  const frame = useCurrentFrame();
  return (
    <Scene label="03 / 资料与证据">
      <div style={{position: 'absolute', left: 120, top: 174, width: 600}}>
        <Reveal delay={4}><BigTitle>结论可以<br />回到出处。</BigTitle></Reveal>
        <Reveal delay={24} style={{marginTop: 34, color: colors.muted, fontSize: 30, lineHeight: 1.5}}>技术观察、关系候选和业务决定分别呈现。证据不足时保持未知。</Reveal>
      </div>
      <div style={{position: 'absolute', left: 790, top: 198, width: 1010, padding: 18, border: `1px solid ${colors.line}`, background: colors.surface, boxShadow: '0 30px 90px rgba(19,36,58,.14)', overflow: 'hidden'}}>
        <Img src={staticFile('demo-preview.jpg')} style={{display: 'block', width: '100%', scale: interpolate(frame, [10, 260], [1.03, 1.09], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp', output: 'perceptual-scale'})}} />
        <div style={{position: 'absolute', right: 38, top: 38, background: 'rgba(19,36,58,.9)', color: 'white', padding: '12px 18px', fontSize: 18}}>公开合成演示</div>
      </div>
    </Scene>
  );
};

const DefinitionChange: React.FC = () => (
  <Scene label="04 / 人确认以后">
    <div style={{position: 'absolute', left: 120, right: 120, top: 150}}>
      <Reveal delay={2}><BigTitle>不是多一段回复，<br />而是定义真的发生变化。</BigTitle></Reveal>
      <div style={{display: 'grid', gridTemplateColumns: '1fr 100px 1fr', alignItems: 'stretch', marginTop: 60}}>
        <Reveal delay={28} style={{background: colors.surface, border: `1px solid ${colors.line}`, padding: 34}}>
          <div style={{color: colors.blue, fontSize: 18}}>确认前</div>
          <div style={{marginTop: 32, fontSize: 30, lineHeight: 1.9}}>退款规则：未知<br />缺失金额：未知<br />表间关系：候选待补充</div>
        </Reveal>
        <Reveal delay={50} style={{display: 'grid', placeItems: 'center', color: colors.blue, fontSize: 46}}>→</Reveal>
        <Reveal delay={66} style={{background: colors.surface, border: `2px solid ${colors.blue}`, padding: 34, boxShadow: `inset 7px 0 ${colors.blue}`}}>
          <div style={{color: colors.blue, fontSize: 18}}>确认后</div>
          <div style={{marginTop: 32, fontSize: 30, lineHeight: 1.9}}>退款规则：扣除同地区已退款金额<br />缺失金额：不计入净额，单独列出<br />仍需确认：本次统计时间范围</div>
        </Reveal>
      </div>
    </div>
  </Scene>
);

const Boundary: React.FC = () => (
  <Scene label="05 / 当前与下一步">
    <div style={{position: 'absolute', left: 120, right: 120, top: 170}}>
      <Reveal delay={3}><BigTitle>先把候选说清楚，<br />再让它进入交付。</BigTitle></Reveal>
      <div style={{display: 'flex', gap: 28, marginTop: 70}}>
        <Card label="CURRENT" title="Demo 1.0.0" body="连续对话、澄清确认、候选成果与引用。" delay={25} />
        <Card label="NEXT" title="正式定义交付" body="完整定义、版本比较、验收条件、审批与回执。" delay={40} />
        <Card label="THEN" title="批准知识复用" body="在新任务中建议并选用仍然有效的批准定义。" delay={55} />
      </div>
    </div>
  </Scene>
);

const Cta: React.FC = () => (
  <Scene label="06 / CONTEXTOX">
    <div style={{position: 'absolute', left: 120, right: 120, top: 190, display: 'grid', gridTemplateColumns: '1fr 400px', alignItems: 'center'}}>
      <div>
        <Reveal delay={2}><BigTitle>带一个真实的问题，<br />和数契一起把它说清楚。</BigTitle></Reveal>
        <Reveal delay={25} style={{marginTop: 52, display: 'inline-block', background: colors.blue, color: 'white', padding: '20px 30px', borderRadius: 10, fontSize: 25, fontWeight: 720}}>github.com/archerthegoat/contextox-agent</Reveal>
      </div>
      <Reveal delay={18}><Img src={staticFile('contextox-mark.png')} style={{width: 310, height: 310}} /></Reveal>
    </div>
  </Scene>
);

export const ContextOxSilentLaunch: React.FC = () => (
  <AbsoluteFill style={{background: colors.paper}}>
    <Sequence from={0} durationInFrames={150}><Hook /></Sequence>
    <Sequence from={150} durationInFrames={180}><Problem /></Sequence>
    <Sequence from={330} durationInFrames={210}><Method /></Sequence>
    <Sequence from={540} durationInFrames={270}><Evidence /></Sequence>
    <Sequence from={810} durationInFrames={240}><DefinitionChange /></Sequence>
    <Sequence from={1050} durationInFrames={180}><Boundary /></Sequence>
    <Sequence from={1230} durationInFrames={120}><Cta /></Sequence>
  </AbsoluteFill>
);
