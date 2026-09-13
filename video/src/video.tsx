import React, {type ReactNode} from 'react';
import {
  AbsoluteFill,
  Audio,
  Easing,
  Img,
  interpolate,
  spring,
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
  lineStrong: '#BCC8D6',
  blue: '#1674F3',
  blueSoft: '#EEF5FF',
  green: '#147D64',
  greenSoft: '#E9F7F2',
  amber: '#A76A08',
  amberSoft: '#FFF0CF',
  red: '#B8493E',
  redSoft: '#FFEAE7',
  stage: '#0D1B2E',
};

const fontFamily = '-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Hiragino Sans GB", sans-serif';

const clamp = (frame: number, input: number[], output: number[]) =>
  interpolate(frame, input, output, {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: Easing.bezier(0.16, 1, 0.3, 1),
  });

const enter = (frame: number, start: number, duration = 18) => clamp(frame, [start, start + duration], [0, 1]);
const exit = (frame: number, start: number, duration = 18) => clamp(frame, [start, start + duration], [1, 0]);

const Grid: React.FC<{dark?: boolean}> = ({dark = false}) => (
  <AbsoluteFill
    style={{
      backgroundColor: dark ? colors.stage : colors.paper,
      backgroundImage: dark
        ? 'linear-gradient(rgba(255,255,255,.055) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,.055) 1px, transparent 1px), radial-gradient(circle at 82% 28%, rgba(22,116,243,.28), transparent 38%)'
        : `linear-gradient(${colors.line} 1px, transparent 1px), linear-gradient(90deg, ${colors.line} 1px, transparent 1px)`,
      backgroundSize: dark ? '72px 72px, 72px 72px, 100% 100%' : '56px 56px',
    }}
  />
);

const Caption: React.FC<{children: ReactNode; dark?: boolean}> = ({children, dark = false}) => (
  <div
    style={{
      position: 'absolute',
      left: 130,
      right: 130,
      bottom: 34,
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      minHeight: 68,
      padding: '12px 32px',
      border: `1px solid ${dark ? 'rgba(255,255,255,.18)' : colors.lineStrong}`,
      background: dark ? 'rgba(13,27,46,.88)' : 'rgba(255,255,255,.94)',
      color: dark ? '#F5F8FC' : colors.ink,
      boxShadow: dark ? 'none' : '0 16px 46px rgba(19,36,58,.12)',
      fontSize: 30,
      fontWeight: 720,
      letterSpacing: -0.5,
      textAlign: 'center',
      zIndex: 50,
    }}
  >
    {children}
  </div>
);

const StatusPill: React.FC<{children: ReactNode; tone?: 'blue' | 'green' | 'amber' | 'red'}> = ({children, tone = 'blue'}) => {
  const palette = {
    blue: [colors.blueSoft, colors.blue],
    green: [colors.greenSoft, colors.green],
    amber: [colors.amberSoft, colors.amber],
    red: [colors.redSoft, colors.red],
  }[tone];
  return <span style={{padding: '5px 9px', borderRadius: 5, background: palette[0], color: palette[1], fontSize: 15, fontWeight: 750}}>{children}</span>;
};

const openingRows = [
  ['O001', '华东', '120.00', 'paid'],
  ['O002', '华南', '80.50', 'paid'],
  ['O003', '华东', '50.00', 'refunded'],
  ['O004', '华东', '199.00', 'pending'],
  ['O005', '华南', '60.00', 'paid'],
  ['O006', '华东', '320.00', 'paid'],
];

const MiniOrderTable: React.FC<{frame: number; focus?: boolean}> = ({frame, focus = false}) => (
  <div style={{border: `1px solid ${focus ? '#6488B4' : 'rgba(255,255,255,.2)'}`, background: focus ? colors.surface : 'rgba(255,255,255,.06)', overflow: 'hidden'}}>
    <div style={{display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1.25fr', padding: focus ? '20px 25px' : '15px 22px', background: focus ? '#E8EEF6' : 'rgba(255,255,255,.08)', color: focus ? colors.muted : '#B8C8DD', fontSize: focus ? 20 : 17, fontWeight: 750}}>
      <span>订单</span><span>地区</span><span>金额</span><span>状态</span>
    </div>
    {openingRows.map((row, index) => {
      const rowOpacity = enter(frame, 4 + index * 5, 10);
      const isRefund = row[3] === 'refunded';
      const isPending = row[3] === 'pending';
      return (
        <div
          key={row[0]}
          style={{
            display: 'grid',
            gridTemplateColumns: '1fr 1fr 1fr 1.25fr',
            alignItems: 'center',
            minHeight: focus ? 67 : 50,
            padding: focus ? '10px 25px' : '7px 22px',
            borderTop: `1px solid ${focus ? colors.line : 'rgba(255,255,255,.12)'}`,
            background: focus && isRefund ? '#FFF4F2' : focus && isPending ? '#FFF8E9' : focus ? colors.surface : 'transparent',
            color: focus ? colors.ink : '#F4F7FB',
            fontSize: focus ? 24 : 19,
            opacity: rowOpacity,
            transform: `translateX(${(1 - rowOpacity) * -30}px)`,
          }}
        >
          <span>{row[0]}</span><strong>{row[1]}</strong><span style={{fontVariantNumeric: 'tabular-nums'}}>{row[2]}</span>
          <span><StatusPill tone={isRefund ? 'red' : isPending ? 'amber' : 'green'}>{row[3]}</StatusPill></span>
        </div>
      );
    })}
  </div>
);

const RushingNumber: React.FC<{frame: number; start: number; value: string; label: string; focus?: boolean}> = ({frame, start, value, label, focus = false}) => {
  const {fps} = useVideoConfig();
  const amount = spring({frame: frame - start, fps, config: {damping: 14, stiffness: 180, mass: 0.72}});
  return (
    <div style={{position: 'relative', display: 'grid', gridTemplateColumns: '1fr auto', alignItems: 'end', padding: '17px 22px', border: `1px solid ${focus ? '#4D9AFF' : 'rgba(255,255,255,.2)'}`, background: focus ? 'rgba(22,116,243,.24)' : 'rgba(255,255,255,.055)', opacity: amount, transform: `translateX(${(1 - amount) * 180}px) scale(${0.88 + amount * 0.12})`}}>
      <strong style={{fontSize: 72, lineHeight: 0.95, letterSpacing: -4}}>{value}</strong>
      <span style={{paddingBottom: 8, color: '#B8C8DD', fontSize: 22}}>元</span>
      <small style={{gridColumn: '1 / -1', marginTop: 10, color: '#B8C8DD', fontSize: 16}}>{label}</small>
    </div>
  );
};

const CaseOpening: React.FC<{frame: number}> = ({frame}) => {
  const firstOpacity = frame < 118 ? exit(frame, 100, 18) : 0;
  const focusOpacity = enter(frame, 106, 18);
  const sceneOpacity = frame < 248 ? 1 : exit(frame, 248, 20);
  const activeRule = frame < 172 ? 'status' : frame < 222 ? 'amount' : 'region';
  return (
    <AbsoluteFill style={{fontFamily, color: 'white', opacity: sceneOpacity}}>
      <Grid dark />
      <div style={{position: 'absolute', inset: 0, opacity: firstOpacity}}>
        <div style={{position: 'absolute', left: 100, top: 62, color: '#81B5FF', fontSize: 18, fontWeight: 750, letterSpacing: 1.3}}>公开合成数据 · 解释案例</div>
        <div style={{position: 'absolute', left: 100, top: 108, fontSize: 54, lineHeight: 1.15, fontWeight: 780, letterSpacing: -2.4}}>同一份订单数据，华东金额到底是多少？</div>
        <div style={{position: 'absolute', left: 100, top: 210, width: 1120}}><MiniOrderTable frame={frame} /></div>
        <div style={{position: 'absolute', right: 100, top: 210, width: 500, display: 'grid', gap: 16}}>
          <RushingNumber frame={frame} start={28} value="689" label="所有记录都相加" />
          <RushingNumber frame={frame} start={54} value="440" label="只统计已支付" />
          <RushingNumber frame={frame} start={80} value="390" label="已支付再扣退款" focus />
        </div>
      </div>
      <div style={{position: 'absolute', inset: 0, opacity: focusOpacity}}>
        <div style={{position: 'absolute', left: 120, top: 65, color: '#81B5FF', fontSize: 18, fontWeight: 750}}>同一份数据 · 规则不同，答案不同</div>
        <div style={{position: 'absolute', left: 120, top: 112, right: 120, display: 'flex', justifyContent: 'space-between', alignItems: 'end'}}>
          <h1 style={{margin: 0, fontSize: 58, lineHeight: 1.06, letterSpacing: -2.2}}>镜头钻进真正会改变答案的地方</h1>
          <div style={{display: 'flex', gap: 10}}>{['status', 'amount', 'region'].map((field) => <span key={field} style={{padding: '10px 15px', border: `1px solid ${field === activeRule ? '#4D9AFF' : 'rgba(255,255,255,.18)'}`, background: field === activeRule ? 'rgba(22,116,243,.28)' : 'rgba(255,255,255,.05)', color: field === activeRule ? 'white' : '#AFC0D3', fontSize: 19, fontWeight: 700}}>{field}</span>)}</div>
        </div>
        <div style={{position: 'absolute', left: 120, right: 120, top: 245, transform: `scale(${clamp(frame, [120, 270], [.96, 1.035])})`, transformOrigin: 'center top'}}>
          <MiniOrderTable frame={frame} focus />
          <div style={{display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12, marginTop: 16}}>
            <div style={{padding: 18, borderLeft: `4px solid ${colors.green}`, background: '#F0FBF7', color: colors.ink}}><small style={{color: colors.green}}>paid</small><strong style={{display: 'block', marginTop: 5, fontSize: 24}}>已支付共 440 元</strong></div>
            <div style={{padding: 18, borderLeft: `4px solid ${colors.amber}`, background: '#FFF9EC', color: colors.ink}}><small style={{color: colors.amber}}>pending</small><strong style={{display: 'block', marginTop: 5, fontSize: 24}}>待支付 199 元算不算？</strong></div>
            <div style={{padding: 18, borderLeft: `4px solid ${colors.red}`, background: '#FFF4F2', color: colors.ink}}><small style={{color: colors.red}}>refunded</small><strong style={{display: 'block', marginTop: 5, fontSize: 24}}>退款 50 元怎么处理？</strong></div>
          </div>
        </div>
      </div>
      {frame < 120 ? <Caption dark>689？440？390？</Caption> : <Caption dark>不是不会算，是规则没说清</Caption>}
    </AbsoluteFill>
  );
};

const Brand: React.FC = () => (
  <div style={{display: 'flex', alignItems: 'center', gap: 13, color: colors.ink}}>
    <Img src={staticFile('contextox-mark.png')} style={{width: 46, height: 46}} />
    <div><strong style={{display: 'block', fontSize: 24, lineHeight: 1.1}}>数契</strong><small style={{color: colors.muted, fontSize: 12, letterSpacing: 1.2}}>CONTEXTOX</small></div>
  </div>
);

const ProgressHeader: React.FC<{phase: number; now: string; next: string; result: string; title?: string}> = ({phase, now, next, result, title = '按地区统计订单金额'}) => {
  const steps = ['明确目标', '理解资料', '澄清口径', '整理成果'];
  return <><div style={{padding: '25px 30px 16px'}}><small style={{color: colors.blue, fontSize: 15, fontWeight: 750}}>当前进展</small><h2 style={{margin: '4px 0 17px', fontSize: 28, fontWeight: 650}}>{title}</h2><div style={{display: 'flex'}}>{steps.map((step, index) => <div key={step} style={{display: 'flex', flex: 1, alignItems: 'center', gap: 8, color: index === phase ? colors.blue : index < phase ? '#587796' : '#8999AA', fontSize: 15, fontWeight: index === phase ? 750 : 600}}><span style={{display: 'grid', width: 25, height: 25, placeItems: 'center', border: `1px solid ${index <= phase ? colors.blue : colors.lineStrong}`, borderRadius: '50%', background: index === phase ? colors.blue : colors.surface, color: index === phase ? 'white' : index < phase ? colors.blue : colors.muted}}>{index < phase ? '✓' : index + 1}</span><span>{step}</span>{index < 3 ? <i style={{height: 1, flex: 1, marginRight: 8, background: index < phase ? '#9AC1F4' : colors.line}} /> : null}</div>)}</div></div><div style={{display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 1, margin: '0 30px 17px', border: `1px solid ${colors.line}`, borderRadius: 9, overflow: 'hidden', background: colors.line}}>{[['正在做什么', now], ['需要你做什么', next], ['已经得到什么', result]].map(([label, value], index) => <div key={label} style={{minHeight: 72, padding: '10px 13px', background: index === 1 ? colors.blueSoft : colors.surface}}><small style={{display: 'block', color: index === 1 ? colors.blue : colors.muted, fontSize: 12}}>{label}</small><strong style={{display: 'block', marginTop: 5, color: index === 1 ? colors.blue : '#496078', fontSize: 15, lineHeight: 1.35}}>{value}</strong></div>)}</div></>;
};

const Navigation: React.FC<{frame: number}> = ({frame}) => {
  const selected = frame < 315 ? 0 : frame < 345 ? 1 : frame < 375 ? 2 : 3;
  return <aside style={{display: 'flex', flexDirection: 'column', padding: '24px 17px 18px', borderRight: `1px solid ${colors.line}`, background: '#F8FAFC'}}><div style={{padding: '0 8px'}}><Brand /></div><div style={{margin: '20px 5px 14px', padding: '10px 11px', border: `1px solid ${colors.lineStrong}`, borderRadius: 7, background: colors.surface}}><small style={{display: 'block', color: colors.muted}}>当前工作区</small><strong style={{fontSize: 15}}>公开示例工作区</strong></div><button style={{margin: '0 5px 22px', padding: '10px 12px', border: '1px solid #C6DCF9', borderRadius: 6, background: colors.surface, color: '#1469D0', fontSize: 15, textAlign: 'left'}}>新对话</button><small style={{padding: '0 8px', color: colors.muted, fontSize: 13}}>最近工作</small><div style={{margin: '8px 5px 21px', padding: '10px', borderRadius: 6, background: colors.blueSoft, color: colors.blue, fontSize: 14, fontWeight: 700}}>按地区统计订单金额</div><div style={{display: 'flex', justifyContent: 'space-between', padding: '0 8px', color: colors.muted, fontSize: 13}}><span>资料库</span><span style={{color: colors.blue}}>添加资料</span></div><div style={{display: 'grid', gap: 5, margin: '8px 5px'}}>{['orders.csv', 'customers.csv', 'notes.md'].map((name, index) => <div key={name} style={{padding: '9px 10px', borderRadius: 5, background: selected > index ? '#EAF3FF' : 'transparent', color: selected > index ? '#205FA8' : '#536579', fontSize: 15, fontWeight: selected > index ? 700 : 500}}>{selected > index ? '✓ ' : '▧ '}{name}</div>)}</div><div style={{marginTop: 'auto', padding: '14px 8px 0', borderTop: `1px solid ${colors.line}`, color: colors.muted, fontSize: 12}}>资料与设置只留在本机</div></aside>;
};

const SourceScope: React.FC<{frame: number}> = ({frame}) => {
  const selected = frame < 315 ? 0 : frame < 345 ? 1 : frame < 375 ? 2 : 3;
  return <div style={{marginTop: 8, padding: 12, border: '1px solid #DBE6F3', borderRadius: 8, background: '#F8FBFF'}}><strong style={{display: 'block', color: '#425772', fontSize: 14}}>本轮资料 {selected} 份 · 历史 0 条</strong><p style={{margin: '6px 0 9px', color: colors.muted, fontSize: 12}}>这里只使用你明确勾选的资料。</p>{['orders.csv', 'customers.csv', 'notes.md'].map((name, index) => {const checked = selected > index; return <div key={name} style={{display: 'flex', alignItems: 'center', gap: 10, minHeight: 41, padding: '5px 7px', borderTop: `1px solid ${colors.line}`}}><span style={{display: 'grid', width: 20, height: 20, placeItems: 'center', border: `1px solid ${checked ? colors.blue : colors.lineStrong}`, borderRadius: 4, background: checked ? colors.blue : colors.surface, color: 'white', fontSize: 13}}>{checked ? '✓' : ''}</span><span style={{fontSize: 14}}>{name} · {checked ? '本轮已选' : '未选择'}</span></div>;})}</div>;
};

const AgentHeader: React.FC = () => <div style={{display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '19px 23px', borderBottom: '1px solid #EDF1F6'}}><div><strong style={{display: 'block', fontSize: 19}}>数契 Agent</strong><span style={{display: 'block', marginTop: 4, color: '#7E8DA0', fontSize: 13}}>一起把资料里的业务意思弄清楚</span></div><span style={{padding: '6px 8px', border: `1px solid ${colors.line}`, borderRadius: 5, color: colors.muted, fontSize: 12}}>展开</span></div>;

const Message: React.FC<{role: 'user' | 'agent'; children: ReactNode}> = ({role, children}) => <div style={{margin: role === 'user' ? '0 0 17px 52px' : '0 0 20px', padding: role === 'user' ? '12px 14px' : 0, borderRadius: role === 'user' ? '9px 9px 2px 9px' : 0, background: role === 'user' ? '#F2F5F9' : 'transparent'}}><div style={{display: 'flex', justifyContent: 'space-between', color: '#72849A', fontSize: 12}}><strong>{role === 'user' ? '你' : '数契 Agent'}</strong><span>{role === 'user' ? '10:31' : '10:32'}</span></div><div style={{marginTop: 7, color: '#314A63', fontSize: 15, lineHeight: 1.55}}>{children}</div></div>;

const Composer: React.FC<{frame: number; compact?: boolean}> = ({frame, compact = false}) => {
  const goal = '我想按地区统计订单金额。';
  const typed = Math.max(0, Math.min(goal.length, Math.floor((frame - 382) / 2.2)));
  return <div style={{padding: compact ? '11px 20px 14px' : '12px 21px 15px', borderTop: '1px solid #EDF1F6', background: colors.surface}}>{!compact ? <SourceScope frame={frame} /> : null}<div style={{minHeight: compact ? 70 : 82, marginTop: 9, padding: 11, border: '1px solid #C4D2E2', borderRadius: 8, color: colors.ink, fontSize: 15, lineHeight: 1.55}}>{frame < 382 ? <span style={{color: '#8A99AA'}}>说说你想弄清什么，也可以先添加资料</span> : <span>{goal.slice(0, typed)}{frame < 440 ? <i style={{display: 'inline-block', width: 2, height: 18, marginLeft: 2, verticalAlign: -3, background: colors.blue}} /> : null}</span>}</div><div style={{display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 8}}><button style={{padding: '7px 10px', border: 0, borderRadius: 6, background: '#F2F6FB', color: '#597491', fontSize: 13}}>资料 · {frame < 315 ? 0 : frame < 345 ? 1 : frame < 375 ? 2 : 3}</button><button style={{padding: '8px 14px', border: 0, borderRadius: 6, background: frame >= 430 ? colors.blue : '#AAC7EC', color: 'white', fontSize: 13, fontWeight: 700}}>发送</button></div></div>;
};

const CenterHome: React.FC = () => <div style={{display: 'grid', alignContent: 'center', flex: 1, padding: '20px 48px 62px'}}><Img src={staticFile('contextox-mark.png')} style={{width: 70, height: 70}} /><small style={{marginTop: 24, color: colors.blue, fontSize: 15, fontWeight: 750}}>从对话开始</small><h3 style={{margin: '10px 0', fontSize: 38, lineHeight: 1.2, letterSpacing: -1.4}}>说出问题，<br />一起找到有依据的答案。</h3><p style={{maxWidth: 560, margin: 0, color: colors.muted, fontSize: 17, lineHeight: 1.65}}>右侧负责讨论和推进；这里解释当前过程、展示资料依据和核对成果。</p></div>;

const QuestionCard: React.FC<{number: string; title: string; body: string; tone: string}> = ({number, title, body, tone}) => <div style={{display: 'grid', gridTemplateColumns: '42px 1fr auto', alignItems: 'center', gap: 14, padding: '15px 16px', border: `1px solid ${colors.line}`, borderRadius: 8, background: colors.surface}}><span style={{display: 'grid', width: 38, height: 38, placeItems: 'center', borderRadius: '50%', background: colors.blueSoft, color: colors.blue, fontSize: 14, fontWeight: 800}}>{number}</span><div><strong style={{fontSize: 16}}>{title}</strong><p style={{margin: '4px 0 0', color: colors.muted, fontSize: 13}}>{body}</p></div><StatusPill tone="amber">{tone}</StatusPill></div>;

const CenterClarifying: React.FC<{frame: number}> = ({frame}) => <div style={{display: 'grid', gridTemplateColumns: '.78fr 1.22fr', gap: 17, padding: '0 30px 28px', overflow: 'hidden'}}><div style={{padding: 19, border: `1px solid ${colors.line}`, borderRadius: 8, background: colors.surface}}><small style={{color: colors.blue, fontSize: 14, fontWeight: 750}}>资料里已经找到</small>{['订单状态：paid、refunded、pending', '连接字段：orders.customer_id ↔ customers.customer_id', '时间字段：paid_at'].map((text, index) => <p key={text} style={{margin: '16px 0 0', color: index === 1 ? colors.ink : colors.muted, fontSize: 15, lineHeight: 1.5, opacity: enter(frame, 470 + index * 18, 12)}}>{text}</p>)}</div><div style={{display: 'grid', gap: 9}}><div style={{opacity: enter(frame, 520, 12)}}><QuestionCard number="01" title="待支付订单要算进去吗？" body="它会决定华东的 199 元是否进入结果。" tone="影响统计范围" /></div><div style={{opacity: enter(frame, 566, 12)}}><QuestionCard number="02" title="退款订单怎么处理？" body="排除退款和从已支付金额中扣除，会得到不同答案。" tone="影响金额规则" /></div><div style={{opacity: enter(frame, 612, 12)}}><QuestionCard number="03" title="按哪个时间归属？" body="支付时间会改变统计范围。" tone="影响时间范围" /></div></div></div>;

const CenterWaiting: React.FC = () => <div style={{display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, padding: '0 30px 28px'}}><div style={{padding: 25, border: `1px solid ${colors.line}`, borderRadius: 9, background: colors.surface}}><small style={{color: colors.blue}}>当前目标</small><h3 style={{margin: '17px 0 8px', fontSize: 24}}>按地区统计订单金额</h3><p style={{margin: 0, color: colors.muted, fontSize: 15}}>资料关系已经找到，业务规则还需要人确认。</p></div><div style={{padding: 25, border: '1px solid #E8D5A8', borderRadius: 9, background: '#FFFAEF'}}><StatusPill tone="amber">等待人的决定</StatusPill><h3 style={{margin: '17px 0 8px', fontSize: 24}}>3 个会改变结果的问题</h3><p style={{margin: 0, color: colors.muted, fontSize: 15}}>回答不会自动采用；核对后点击“确认并继续”。</p></div></div>;

const AgentSelecting: React.FC<{frame: number}> = ({frame}) => <div style={{display: 'flex', flex: 1, minHeight: 0, flexDirection: 'column'}}><div style={{padding: '18px 23px 6px'}}><p style={{margin: 0, color: '#526C86', fontSize: 15, lineHeight: 1.65}}>你好，我可以和你一起把资料里的业务规则弄清楚。</p></div><div style={{flex: 1}} /><Composer frame={frame} /></div>;

const AgentClarifying: React.FC<{frame: number}> = ({frame}) => <div style={{display: 'flex', flex: 1, minHeight: 0, flexDirection: 'column'}}><div style={{flex: 1, padding: '20px 23px', overflow: 'hidden'}}><Message role="user">我想按地区统计订单金额。</Message><div style={{opacity: enter(frame, 474, 14)}}><Message role="agent">我找到了订单状态、金额、支付时间，以及订单和客户的连接字段。</Message></div><div style={{opacity: enter(frame, 530, 14)}}><Message role="agent">先确认：待支付订单要算进去吗？它会影响华东的 199 元。</Message></div><div style={{opacity: enter(frame, 586, 14)}}><Message role="agent">退款是排除，还是从原订单地区的已支付金额中扣除？</Message></div><div style={{opacity: enter(frame, 626, 14), padding: '11px 13px', borderLeft: '3px solid #BAD3F5', color: '#51647D', fontSize: 13}}>已找到需要业务判断的事项 · 等待回答</div></div><Composer frame={450} compact /></div>;

const AnswerRow: React.FC<{label: string; value: string; note: string; active: boolean}> = ({label, value, note, active}) => <div style={{padding: '12px 11px', borderTop: `1px solid ${colors.line}`, background: active ? colors.blueSoft : 'transparent'}}><small style={{color: colors.muted, fontSize: 11}}>{label}</small><strong style={{display: 'block', marginTop: 3, color: active ? colors.blue : colors.ink, fontSize: 14, lineHeight: 1.35}}>{value}</strong><span style={{display: 'block', marginTop: 3, color: colors.muted, fontSize: 11}}>{note}</span></div>;

const AgentConfirming: React.FC<{frame: number}> = ({frame}) => {
  const active = frame < 724 ? 0 : frame < 770 ? 1 : frame < 816 ? 2 : 3;
  return <div style={{display: 'flex', flex: 1, minHeight: 0, flexDirection: 'column', padding: '15px 20px 18px'}}><div style={{padding: 16, border: '1px solid #D7E3F4', borderRadius: 8, background: '#FAFCFF', boxShadow: '0 12px 28px rgba(19,36,58,.08)'}}><strong style={{color: '#2F4D6D', fontSize: 16}}>需要你确认的业务口径</strong><p style={{margin: '7px 0 12px', color: '#7589A0', fontSize: 12}}>Agent 已把对话整理成卡片。只有确认以后才会采用。</p><AnswerRow label="纳入哪些订单" value="只统计已支付订单" note="pending 不进入金额" active={active === 0} /><AnswerRow label="退款怎么处理" value="从原订单地区的已支付金额中扣除" note="refunded 作为扣减项" active={active === 1} /><AnswerRow label="按哪个时间" value="按支付时间归属" note="未支付订单没有支付时间" active={active === 2} /><button style={{width: '100%', marginTop: 14, padding: '11px 14px', border: 0, borderRadius: 6, background: colors.blue, color: 'white', fontSize: 15, fontWeight: 750, transform: `scale(${frame >= 834 && frame < 853 ? .97 : 1})`}}>确认并继续</button></div><p style={{margin: '12px 3px 0', color: colors.muted, fontSize: 12}}>批准、查看和刷新不会调用模型。</p></div>;
};

const CandidateCenter: React.FC<{frame: number}> = ({frame}) => <div style={{padding: '0 30px 30px', overflow: 'hidden'}}><div style={{display: 'flex', gap: 8, marginBottom: 12}}><span style={{padding: '8px 12px', borderBottom: `3px solid ${colors.blue}`, color: colors.blue, fontSize: 14, fontWeight: 750}}>关系与字段</span><span style={{padding: '8px 12px', color: colors.muted, fontSize: 14}}>过程记录</span></div><div style={{display: 'grid', gridTemplateColumns: '.88fr 1.12fr', gap: 14}}><div style={{padding: 20, border: `1px solid ${colors.line}`, borderRadius: 9, background: colors.surface, opacity: enter(frame, 862, 15)}}><small style={{color: colors.blue, fontWeight: 750}}>候选字段</small><h3 style={{margin: '13px 0 5px', fontSize: 27}}>地区净订单金额</h3><p style={{margin: 0, color: colors.muted, fontSize: 14, lineHeight: 1.55}}>按客户地区汇总已支付金额，再扣除同一地区的退款金额。</p><div style={{display: 'grid', gap: 7, marginTop: 16}}>{['只统计已支付订单', '退款从原订单地区扣除', '按支付时间归属'].map((rule) => <div key={rule} style={{display: 'flex', justifyContent: 'space-between', padding: '9px 10px', background: '#F6FAFF', fontSize: 13}}><span>{rule}</span><StatusPill tone="green">已确认</StatusPill></div>)}</div></div><div style={{padding: 20, border: `1px solid ${colors.line}`, borderRadius: 9, background: colors.surface, opacity: enter(frame, 892, 15)}}><small style={{color: colors.blue, fontWeight: 750}}>候选关系</small><h3 style={{margin: '13px 0 5px', fontSize: 22}}>orders 与 customers</h3><div style={{display: 'grid', gridTemplateColumns: '1fr 55px 1fr', alignItems: 'center', marginTop: 25}}><div style={{padding: 15, border: '1px solid #C6D9F0', borderRadius: 7, background: '#F7FAFF'}}><strong>orders</strong><span style={{display: 'block', marginTop: 6, color: colors.muted, fontSize: 13}}>customer_id</span></div><div style={{color: colors.blue, fontSize: 25, textAlign: 'center'}}>↔</div><div style={{padding: 15, border: '1px solid #C6D9F0', borderRadius: 7, background: '#F7FAFF'}}><strong>customers</strong><span style={{display: 'block', marginTop: 6, color: colors.muted, fontSize: 13}}>customer_id</span></div></div><div style={{marginTop: 25, padding: 12, borderLeft: `3px solid ${colors.blue}`, background: colors.paper, color: colors.muted, fontSize: 13}}>来源：orders.csv、customers.csv、notes.md</div></div></div></div>;

const AgentReview: React.FC<{frame: number}> = ({frame}) => <div style={{display: 'flex', flex: 1, minHeight: 0, flexDirection: 'column'}}><div style={{flex: 1, padding: '21px 23px'}}><Message role="agent">回答已经采用，候选成果已更新。现在可以核对字段、资料关系和仍未知的事项。</Message><section style={{display: 'grid', gap: 8, marginTop: 25, padding: 15, border: '1px solid #D6E3F3', borderRadius: 9, background: '#F8FBFF'}}><small style={{color: colors.blue, fontWeight: 750}}>建议下一步</small><strong style={{color: '#304A65', fontSize: 15}}>核对候选成果和仍未知的事项</strong><button style={{marginTop: 5, padding: '10px 12px', border: 0, borderRadius: 6, background: colors.blue, color: 'white', fontSize: 14, fontWeight: 750, transform: `scale(${frame >= 1094 && frame < 1112 ? .97 : 1})`}}>查看本轮变化</button><div style={{display: 'flex', gap: 8}}><span style={{padding: '7px 9px', border: `1px solid ${colors.line}`, borderRadius: 5, color: colors.muted, fontSize: 12}}>继续补充口径</span><span style={{padding: '7px 9px', border: `1px solid ${colors.line}`, borderRadius: 5, color: colors.muted, fontSize: 12}}>查看过程记录</span></div></section></div><Composer frame={450} compact /></div>;

const ChangesCenter: React.FC<{frame: number}> = ({frame}) => <div style={{padding: '0 30px 30px'}}><div style={{padding: 18, border: `1px solid ${colors.line}`, borderRadius: 9, background: colors.surface, opacity: enter(frame, 1070, 14)}}><div style={{display: 'flex', justifyContent: 'space-between', alignItems: 'start'}}><div><small style={{color: colors.blue, fontWeight: 750}}>本轮更新</small><h3 style={{margin: '6px 0 4px', fontSize: 25}}>候选成果已经更新</h3><p style={{margin: 0, color: colors.muted, fontSize: 13}}>这里展示已采用的业务回答、它带来的变化和仍待确认的事项。</p></div><StatusPill tone="green">可回看</StatusPill></div><div style={{display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 10, marginTop: 17}}><div style={{padding: 13, background: colors.blueSoft}}><small style={{color: colors.blue}}>规则变化</small><strong style={{display: 'block', marginTop: 6, fontSize: 15}}>纳入、退款、时间已写清</strong></div><div style={{padding: 13, background: '#F4F8FD'}}><small style={{color: colors.muted}}>证据位置</small><strong style={{display: 'block', marginTop: 6, fontSize: 15}}>3 份资料可回到出处</strong></div><div style={{padding: 13, background: '#FFF9EC'}}><small style={{color: colors.amber}}>仍未知</small><strong style={{display: 'block', marginTop: 6, fontSize: 15}}>跨月退款边界待补充</strong></div></div><div style={{display: 'grid', gap: 9, marginTop: 14}}>{[['纳入范围', 'status = paid', '人的回答'], ['退款处理', '从原订单地区扣除', '人的回答'], ['资料关系', 'orders.customer_id = customers.customer_id', '表结构观察']].map(([label, value, source], index) => <div key={label} style={{display: 'grid', gridTemplateColumns: '125px 1fr 125px', alignItems: 'center', gap: 10, padding: '10px 12px', borderTop: `1px solid ${colors.line}`, opacity: enter(frame, 1088 + index * 22, 12)}}><small style={{color: colors.muted}}>{label}</small><strong style={{fontSize: 14}}>{value}</strong><span style={{color: index === 2 ? colors.blue : colors.green, fontSize: 12}}>{source}</span></div>)}</div></div></div>;

const CenterPanel: React.FC<{frame: number}> = ({frame}) => {
  if (frame < 450) return <><ProgressHeader phase={0} title="从一个问题开始" now="等待你的目标" next="选择资料并说出问题" result={`${frame < 315 ? 0 : frame < 345 ? 1 : frame < 375 ? 2 : 3} 份资料进入本轮`} /><CenterHome /></>;
  if (frame < 660) return <><ProgressHeader phase={frame < 520 ? 1 : 2} now={frame < 520 ? '正在理解资料' : '已找到需要业务判断的事项'} next={frame < 520 ? '稍等片刻' : '回答 3 个会改变结果的问题'} result="字段、状态和资料关系已找到" /><CenterClarifying frame={frame} /></>;
  if (frame < 870) return <><ProgressHeader phase={2} now="等待业务回答" next="核对并确认 3 条规则" result="候选字段与关系已经保留" /><CenterWaiting /></>;
  if (frame < 1080) return <><ProgressHeader phase={3} now="候选成果已经更新" next="核对变化和仍未知的事项" result="1 个字段 · 1 条关系 · 1 项未知" /><CandidateCenter frame={frame} /></>;
  return <><ProgressHeader phase={3} now="本轮变化已展开" next="核对出处与未知事项" result="回答、变化和证据可以回看" /><ChangesCenter frame={frame} /></>;
};

const AgentPanel: React.FC<{frame: number}> = ({frame}) => <aside style={{display: 'flex', minWidth: 0, minHeight: 0, flexDirection: 'column', borderLeft: `1px solid ${colors.line}`, background: colors.surface}}><AgentHeader />{frame < 450 ? <AgentSelecting frame={frame} /> : frame < 660 ? <AgentClarifying frame={frame} /> : frame < 870 ? <AgentConfirming frame={frame} /> : <AgentReview frame={frame} />}</aside>;

const clickStrength = (frame: number, clicks: number[]) => Math.max(0, ...clicks.map((click) => clamp(Math.abs(frame - click), [0, 7], [1, 0])));

const Cursor: React.FC<{frame: number}> = ({frame}) => {
  let visible = false; let x = 0; let y = 0; let clicks: number[] = [];
  if (frame >= 284 && frame < 450) {visible = true; clicks = [315, 345, 375, 438]; x = clamp(frame, [284, 306, 315, 332, 345, 362, 375, 402, 438, 449], [1570, 1286, 1286, 1286, 1286, 1286, 1286, 1450, 1784, 1784]); y = clamp(frame, [284, 315, 332, 345, 362, 375, 402, 438, 449], [540, 684, 725, 725, 766, 766, 840, 916, 916]);}
  else if (frame >= 675 && frame < 870) {visible = true; clicks = [704, 750, 796, 844]; x = clamp(frame, [675, 704, 720, 750, 766, 796, 820, 844, 869], [1390, 1415, 1415, 1415, 1415, 1415, 1520, 1540, 1540]); y = clamp(frame, [675, 704, 720, 750, 766, 796, 820, 844, 869], [220, 285, 365, 365, 445, 445, 505, 532, 532]);}
  else if (frame >= 1060 && frame < 1130) {visible = true; clicks = [1102]; x = clamp(frame, [1060, 1092, 1102, 1129], [1470, 1540, 1540, 1540]); y = clamp(frame, [1060, 1092, 1102, 1129], [430, 340, 340, 340]);}
  if (!visible) return null;
  const pulse = clickStrength(frame, clicks);
  return <div style={{position: 'absolute', left: x, top: y, zIndex: 60, pointerEvents: 'none'}}><div style={{position: 'absolute', left: -18, top: -18, width: 42, height: 42, border: `3px solid rgba(22,116,243,${.7 * pulse})`, borderRadius: '50%', transform: `scale(${1 + (1 - pulse) * .85})`, opacity: pulse}} /><div style={{width: 34, height: 43, background: colors.ink, clipPath: 'polygon(0 0, 0 92%, 26% 70%, 43% 100%, 58% 91%, 42% 65%, 74% 65%)', filter: 'drop-shadow(0 1px 1px white) drop-shadow(0 3px 5px rgba(0,0,0,.4))', transform: `scale(${pulse > .45 ? .84 : 1})`}} /></div>;
};

const AppSequence: React.FC<{frame: number}> = ({frame}) => {
  const appIn = enter(frame, 248, 22); const appOut = frame < 1220 ? 1 : exit(frame, 1220, 35); const opacity = appIn * (.18 + .82 * appOut); const scale = .91 + appIn * .09 - (1 - appOut) * .055;
  const caption = frame < 450 ? '选资料，说目标' : frame < 660 ? 'Agent 找到需要确认的规则' : frame < 870 ? '关键规则，人来定' : frame < 1080 ? '回答变成候选定义' : '变化、出处、未知，都能回看';
  return <AbsoluteFill style={{fontFamily, color: colors.ink}}><Grid /><div style={{position: 'absolute', left: 76, top: 24, color: colors.blue, fontSize: 16, fontWeight: 750, letterSpacing: .7}}>讲解模拟 · 对应当前 Workbench · 公开合成资料</div><div style={{position: 'absolute', right: 76, top: 24, color: colors.muted, fontSize: 14}}>数字案例只解释规则差异 · 产品画面停在候选定义</div><div style={{position: 'absolute', left: 76, top: 62, width: 1768, height: 888, display: 'grid', gridTemplateColumns: '252px minmax(0,1fr) 610px', overflow: 'hidden', border: `1px solid ${colors.lineStrong}`, background: '#F7F9FB', boxShadow: '0 32px 86px rgba(19,36,58,.18)', opacity, transform: `scale(${scale})`, transformOrigin: 'center center'}}><Navigation frame={frame} /><main style={{display: 'flex', minWidth: 0, minHeight: 0, flexDirection: 'column', overflow: 'hidden', background: '#F7F9FB'}}><CenterPanel frame={frame} /></main><AgentPanel frame={frame} /></div><Cursor frame={frame} />{frame < 1230 ? <Caption>{caption}</Caption> : null}</AbsoluteFill>;
};

const Outro: React.FC<{frame: number}> = ({frame}) => {
  const amount = enter(frame, 1220, 24);
  return <AbsoluteFill style={{fontFamily, color: 'white', opacity: amount}}><div style={{position: 'absolute', inset: 0, background: 'rgba(13,27,46,.94)'}} /><div style={{position: 'absolute', inset: 0, backgroundImage: 'linear-gradient(rgba(255,255,255,.055) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,.055) 1px, transparent 1px), radial-gradient(circle at 78% 32%, rgba(22,116,243,.33), transparent 40%)', backgroundSize: '72px 72px, 72px 72px, 100% 100%'}} /><div style={{position: 'absolute', left: 150, right: 150, top: 185, display: 'grid', gridTemplateColumns: '1fr 340px', alignItems: 'center', gap: 90, transform: `translateY(${(1 - amount) * 34}px)`}}><div><div style={{color: '#81B5FF', fontSize: 20, fontWeight: 750}}>数契 ContextOx · Demo 1.0.0</div><h1 style={{margin: '34px 0 0', fontSize: 78, lineHeight: 1.12, letterSpacing: -3.5}}>数契，<br />把表里的业务意思说清楚</h1><p style={{margin: '38px 0 0', color: '#C7D4E5', fontSize: 25}}>Agent 找证据 · 人确认关键规则 · 候选结果可以回看</p></div><div style={{display: 'grid', placeItems: 'center'}}><Img src={staticFile('contextox-mark.png')} style={{width: 280, height: 280, filter: 'drop-shadow(0 24px 70px rgba(22,116,243,.28))'}} /></div></div><div style={{position: 'absolute', left: 150, right: 150, bottom: 78, display: 'flex', justifyContent: 'space-between', color: '#9FB0C5', fontSize: 16}}><span>公开合成 Demo · 候选而非正式批准结果</span><span>github.com/archerthegoat/contextox-agent</span></div></AbsoluteFill>;
};

export const ContextOxProductFilm: React.FC = () => {
  const frame = useCurrentFrame();
  return <AbsoluteFill style={{background: colors.paper, fontFamily}}><Audio src={staticFile('audio/contextox-v2-mix.wav')} endAt={1348} />{frame < 270 ? <CaseOpening frame={frame} /> : null}{frame >= 240 ? <AppSequence frame={frame} /> : null}{frame >= 1215 ? <Outro frame={frame} /> : null}</AbsoluteFill>;
};
