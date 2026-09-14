import React from 'react';
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

type HookVariant = 'A' | 'B';

type SceneTiming = {
  from: number;
  duration: number;
};

type HookProfile = {
  music: string;
  musicGain: number;
  musicStartFrame: number;
  estimatedBpm: number;
  scenes: {
    eastOrders: SceneTiming;
    threeRules: SceneTiming;
    plainLanguage: SceneTiming;
    workbenchEntry: SceneTiming;
  };
};

type Rect = {
  x: number;
  y: number;
  width: number;
  height: number;
  radius: number;
};

const WIDTH = 1920;
const HEIGHT = 1080;

const COLORS = {
  navy: '#0D1B2E',
  paper: '#F4F1EA',
  surface: '#FFFFFF',
  ink: '#13243A',
  muted: '#667487',
  line: '#CCD5E0',
  blue: '#1674F3',
  blueSoft: '#EAF3FF',
  green: '#147D64',
  greenSoft: '#EAF7F2',
  amber: '#A76A08',
  amberSoft: '#FFF2D4',
  red: '#B8493E',
  redSoft: '#FFE8E4',
};

const FONT_FAMILY =
  '-apple-system, BlinkMacSystemFont, "SF Pro Display", "PingFang SC", "Microsoft YaHei", sans-serif';

const PROFILES: Record<HookVariant, HookProfile> = {
  A: {
    music: 'audio/v4/dirty-thinkin.mp3',
    musicGain: 0.78,
    musicStartFrame: 165,
    estimatedBpm: 120,
    scenes: {
      eastOrders: {from: 0, duration: 75},
      threeRules: {from: 75, duration: 120},
      plainLanguage: {from: 195, duration: 30},
      workbenchEntry: {from: 225, duration: 135},
    },
  },
  B: {
    music: 'audio/v4/night-patrol.mp3',
    musicGain: 0.74,
    musicStartFrame: 0,
    estimatedBpm: 100,
    scenes: {
      eastOrders: {from: 0, duration: 72},
      threeRules: {from: 72, duration: 126},
      plainLanguage: {from: 198, duration: 36},
      workbenchEntry: {from: 234, duration: 126},
    },
  },
};

const TABLE = {
  x: 120,
  y: 310,
  width: 1176,
  headerHeight: 72,
  rowHeight: 100,
  columns: [
    {key: 'order', label: '订单', x: 0, width: 220},
    {key: 'region', label: '地区', x: 220, width: 260},
    {key: 'status', label: '状态', x: 480, width: 316},
    {key: 'amount', label: '金额', x: 796, width: 380},
  ],
};

const RESULT_CARD: Rect = {
  x: 1390,
  y: 456,
  width: 410,
  height: 230,
  radius: 20,
};

const ORDERS = [
  {order: 'O001', region: '华东', status: '已支付', amount: 120, state: 'paid'},
  {order: 'O003', region: '华东', status: '已退款', amount: 50, state: 'refunded'},
  {order: 'O004', region: '华东', status: '待支付', amount: 199, state: 'pending'},
  {order: 'O006', region: '华东', status: '已支付', amount: 320, state: 'paid'},
] as const;

const SOURCE_WIDTH = workbenchLayout.capture.width;
const SOURCE_HEIGHT = workbenchLayout.capture.height;
const SCALE_X = WIDTH / SOURCE_WIDTH;
const SCALE_Y = HEIGHT / SOURCE_HEIGHT;

const WORKBENCH_RECTS = {
  orders: scaleRect(workbenchLayout.regions.ordersSource),
  customers: scaleRect(workbenchLayout.regions.customersSource),
  notes: scaleRect(workbenchLayout.regions.notesSource),
  goal: scaleRect(workbenchLayout.regions.goalInput),
  send: scaleRect(workbenchLayout.regions.sendButton),
};

function scaleRect(rect: Rect): Rect {
  return {
    x: rect.x * SCALE_X,
    y: rect.y * SCALE_Y,
    width: rect.width * SCALE_X,
    height: rect.height * SCALE_Y,
    radius: rect.radius * Math.min(SCALE_X, SCALE_Y),
  };
}

const clampInterpolation = {
  extrapolateLeft: 'clamp' as const,
  extrapolateRight: 'clamp' as const,
};

const easeOut = Easing.out(Easing.cubic);
const easeInOut = Easing.inOut(Easing.cubic);

function progress(frame: number, start: number, end: number, easing = easeOut): number {
  return interpolate(frame, [start, end], [0, 1], {
    ...clampInterpolation,
    easing,
  });
}

function rangeOpacity(frame: number, start: number, fadeIn: number, fadeOut: number, end: number): number {
  const entering = progress(frame, start, fadeIn);
  const leaving = interpolate(frame, [fadeOut, end], [1, 0], clampInterpolation);
  return Math.min(entering, leaving);
}

function amountRect(rowIndex: number): Rect {
  const amountColumn = TABLE.columns[3];
  return {
    x: TABLE.x + amountColumn.x,
    y: TABLE.y + TABLE.headerHeight + rowIndex * TABLE.rowHeight,
    width: amountColumn.width,
    height: TABLE.rowHeight,
    radius: 0,
  };
}

function statusRect(rowIndex: number): Rect {
  const statusColumn = TABLE.columns[2];
  return {
    x: TABLE.x + statusColumn.x,
    y: TABLE.y + TABLE.headerHeight + rowIndex * TABLE.rowHeight,
    width: statusColumn.width,
    height: TABLE.rowHeight,
    radius: 0,
  };
}

const cellCenterY = (rowIndex: number) =>
  TABLE.y + TABLE.headerHeight + rowIndex * TABLE.rowHeight + TABLE.rowHeight / 2;

const Disclosure: React.FC<{onWorkbench?: boolean}> = ({onWorkbench = false}) => (
  <div
    style={{
      position: 'absolute',
      left: 72,
      bottom: 28,
      zIndex: 80,
      color: onWorkbench ? COLORS.ink : 'rgba(255,255,255,0.74)',
      background: onWorkbench ? 'rgba(255,255,255,0.92)' : 'transparent',
      border: onWorkbench ? '1px solid rgba(13,27,46,0.16)' : 'none',
      borderRadius: onWorkbench ? 8 : 0,
      padding: onWorkbench ? '8px 12px' : 0,
      fontFamily: FONT_FAMILY,
      fontSize: 30,
      lineHeight: 1.25,
      letterSpacing: 0.2,
    }}
  >
    公开合成演示 · 三个数字不是产品运行结果
  </div>
);

const QuestionHeader: React.FC<{compact?: boolean}> = ({compact = false}) => (
  <>
    <div
      style={{
        position: 'absolute',
        left: 120,
        top: compact ? 62 : 64,
        fontFamily: FONT_FAMILY,
        fontSize: 28,
        fontWeight: 650,
        color: '#8FB8F3',
        letterSpacing: 1.4,
      }}
    >
      华东 · 公开合成订单
    </div>
    <div
      style={{
        position: 'absolute',
        left: 120,
        top: compact ? 112 : 116,
        fontFamily: FONT_FAMILY,
        fontSize: compact ? 56 : 72,
        lineHeight: 1.08,
        fontWeight: 760,
        letterSpacing: -2.2,
        color: COLORS.surface,
      }}
    >
      {compact ? '同一份订单，三种规则' : '同一份订单，华东到底多少？'}
    </div>
  </>
);

const StatusBadge: React.FC<{state: string; label: string}> = ({state, label}) => {
  const tones =
    state === 'paid'
      ? {background: COLORS.greenSoft, color: COLORS.green}
      : state === 'refunded'
        ? {background: COLORS.redSoft, color: COLORS.red}
        : {background: COLORS.amberSoft, color: COLORS.amber};
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        height: 40,
        borderRadius: 8,
        padding: '0 16px',
        background: tones.background,
        color: tones.color,
        fontSize: 25,
        fontWeight: 720,
      }}
    >
      {label}
    </span>
  );
};

const OrderTable: React.FC<{
  frame: number;
  revealRows?: boolean;
  activeRows?: number[];
}> = ({frame, revealRows = false, activeRows}) => {
  return (
    <div
      style={{
        position: 'absolute',
        left: TABLE.x,
        top: TABLE.y,
        width: TABLE.width,
        height: TABLE.headerHeight + ORDERS.length * TABLE.rowHeight,
        background: COLORS.surface,
        border: `1px solid ${COLORS.line}`,
        borderRadius: 18,
        overflow: 'hidden',
        boxShadow: '0 24px 70px rgba(0,0,0,0.20)',
        fontFamily: FONT_FAMILY,
        color: COLORS.ink,
      }}
    >
      <div
        style={{
          position: 'absolute',
          inset: '0 0 auto 0',
          height: TABLE.headerHeight,
          background: '#E8EDF3',
          borderBottom: `1px solid ${COLORS.line}`,
        }}
      />
      {TABLE.columns.map((column, columnIndex) => (
        <div
          key={column.key}
          style={{
            position: 'absolute',
            left: column.x,
            top: 0,
            width: column.width,
            height: TABLE.headerHeight,
            display: 'flex',
            alignItems: 'center',
            paddingLeft: 28,
            boxSizing: 'border-box',
            borderLeft: columnIndex === 0 ? 'none' : `1px solid ${COLORS.line}`,
            color: COLORS.muted,
            fontSize: 24,
            fontWeight: 700,
            letterSpacing: 1.2,
          }}
        >
          {column.label}
        </div>
      ))}
      {ORDERS.map((order, rowIndex) => {
        const reveal = revealRows ? progress(frame, 7 + rowIndex * 7, 20 + rowIndex * 7) : 1;
        const rowOpacity = activeRows && !activeRows.includes(rowIndex) ? 0.24 : 1;
        const values: React.ReactNode[] = [
          <span key="order" style={{fontWeight: 740}}>{order.order}</span>,
          <span key="region">{order.region}</span>,
          <StatusBadge key="status" state={order.state} label={order.status} />,
          <span key="amount" style={{fontSize: 37, fontWeight: 780}}>¥{order.amount}</span>,
        ];

        return (
          <div
            key={order.order}
            style={{
              position: 'absolute',
              left: 0,
              top: TABLE.headerHeight + rowIndex * TABLE.rowHeight,
              width: TABLE.width,
              height: TABLE.rowHeight,
              opacity: rowOpacity,
              clipPath: `inset(0 ${(1 - reveal) * 100}% 0 0)`,
              background: rowIndex % 2 === 0 ? COLORS.surface : '#FAFBFC',
              borderBottom: rowIndex === ORDERS.length - 1 ? 'none' : `1px solid ${COLORS.line}`,
            }}
          >
            {TABLE.columns.map((column, columnIndex) => (
              <div
                key={column.key}
                style={{
                  position: 'absolute',
                  left: column.x,
                  top: 0,
                  width: column.width,
                  height: TABLE.rowHeight,
                  display: 'flex',
                  alignItems: 'center',
                  paddingLeft: 28,
                  boxSizing: 'border-box',
                  borderLeft: columnIndex === 0 ? 'none' : `1px solid ${COLORS.line}`,
                  fontSize: 29,
                }}
              >
                {values[columnIndex]}
              </div>
            ))}
          </div>
        );
      })}
    </div>
  );
};

const ExactTraceRect: React.FC<{
  rect: Rect;
  color: string;
  fill: string;
  drawProgress: number;
}> = ({rect, color, fill, drawProgress}) => (
  <>
    <rect
      x={rect.x + 2}
      y={rect.y + 2}
      width={rect.width - 4}
      height={rect.height - 4}
      rx={Math.max(0, rect.radius - 2)}
      fill={fill}
      fillOpacity={drawProgress * 0.22}
    />
    <rect
      x={rect.x + 2}
      y={rect.y + 2}
      width={rect.width - 4}
      height={rect.height - 4}
      rx={Math.max(0, rect.radius - 2)}
      fill="none"
      stroke={color}
      strokeWidth={4}
      pathLength={1}
      strokeDasharray={1}
      strokeDashoffset={1 - drawProgress}
      strokeLinecap="square"
      strokeLinejoin="round"
    />
  </>
);

type RuleDefinition = {
  label: string;
  result: string;
  formula: string;
  rows: number[];
  highlightRows: number[];
  negativeRows?: number[];
  accent: string;
  accentSoft: string;
};

const RULES: RuleDefinition[] = [
  {
    label: '全部状态',
    result: '689 元',
    formula: '120 + 50 + 199 + 320',
    rows: [0, 1, 2, 3],
    highlightRows: [0, 1, 2, 3],
    accent: COLORS.blue,
    accentSoft: COLORS.blueSoft,
  },
  {
    label: '只算已支付',
    result: '440 元',
    formula: '120 + 320',
    rows: [0, 3],
    highlightRows: [0, 3],
    accent: COLORS.green,
    accentSoft: COLORS.greenSoft,
  },
  {
    label: '已支付再扣退款',
    result: '390 元',
    formula: '120 + 320 − 50',
    rows: [0, 3, 1],
    highlightRows: [0, 1, 3],
    negativeRows: [1],
    accent: COLORS.green,
    accentSoft: COLORS.greenSoft,
  },
];

const RuleGeometry: React.FC<{
  rule: RuleDefinition;
  stageFrame: number;
}> = ({rule, stageFrame}) => {
  const rectProgress = progress(stageFrame, 1, 7);
  const resultOpacity = progress(stageFrame, 7, 9);
  const resultLinkProgress = progress(stageFrame, 8, 10, easeInOut);
  const railProgress = progress(stageFrame, 10, 13, easeInOut);
  const branchProgress = progress(stageFrame, 12, 15, easeInOut);
  const targetYs = rule.rows.map(cellCenterY);
  const railX = 1348;
  const resultCenterY = RESULT_CARD.y + RESULT_CARD.height / 2;
  const minY = Math.min(...targetYs, resultCenterY);
  const maxY = Math.max(...targetYs, resultCenterY);
  const cellEdgeX = TABLE.x + TABLE.width;

  return (
    <>
      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        style={{position: 'absolute', inset: 0, width: WIDTH, height: HEIGHT, overflow: 'visible'}}
      >
        {rule.rows.flatMap((rowIndex) => {
          const isNegative = rule.negativeRows?.includes(rowIndex) ?? false;
          const color = isNegative ? COLORS.red : rule.accent;
          const fill = isNegative ? COLORS.redSoft : rule.accentSoft;
          return [
            <ExactTraceRect
              key={`status-${rowIndex}`}
              rect={statusRect(rowIndex)}
              color={color}
              fill={fill}
              drawProgress={rectProgress}
            />,
            <ExactTraceRect
              key={`amount-${rowIndex}`}
              rect={amountRect(rowIndex)}
              color={color}
              fill={fill}
              drawProgress={rectProgress}
            />,
          ];
        })}
        {branchProgress > 0
          ? rule.rows.map((rowIndex) => (
              <line
                key={`branch-${rowIndex}`}
                x1={railX}
                y1={cellCenterY(rowIndex)}
                x2={railX + (cellEdgeX - railX) * branchProgress}
                y2={cellCenterY(rowIndex)}
                stroke={rule.negativeRows?.includes(rowIndex) ? COLORS.red : rule.accent}
                strokeWidth={4}
                strokeLinecap="square"
              />
            ))
          : null}
        {railProgress > 0 ? (
          <>
            <line
              x1={railX}
              y1={resultCenterY}
              x2={railX}
              y2={resultCenterY + (minY - resultCenterY) * railProgress}
              stroke={rule.accent}
              strokeWidth={4}
              strokeLinecap="square"
            />
            <line
              x1={railX}
              y1={resultCenterY}
              x2={railX}
              y2={resultCenterY + (maxY - resultCenterY) * railProgress}
              stroke={rule.accent}
              strokeWidth={4}
              strokeLinecap="square"
            />
          </>
        ) : null}
        {resultLinkProgress > 0 ? (
          <line
            x1={RESULT_CARD.x}
            y1={resultCenterY}
            x2={RESULT_CARD.x + (railX - RESULT_CARD.x) * resultLinkProgress}
            y2={resultCenterY}
            stroke={rule.accent}
            strokeWidth={4}
            strokeLinecap="square"
          />
        ) : null}
      </svg>
      <div
        style={{
          position: 'absolute',
          left: RESULT_CARD.x,
          top: RESULT_CARD.y,
          width: RESULT_CARD.width,
          height: RESULT_CARD.height,
          boxSizing: 'border-box',
          borderRadius: RESULT_CARD.radius,
          border: `2px solid ${rule.accent}`,
          background: COLORS.surface,
          padding: '30px 34px',
          opacity: resultOpacity,
          transform: `translateX(${(1 - resultOpacity) * 12}px)`,
          fontFamily: FONT_FAMILY,
          color: COLORS.ink,
        }}
      >
        <div style={{fontSize: 27, color: rule.accent, fontWeight: 760}}>{rule.label}</div>
        <div style={{marginTop: 10, fontSize: 70, lineHeight: 1, fontWeight: 820, letterSpacing: -2.5}}>
          {rule.result}
        </div>
        <div style={{marginTop: 20, fontSize: 24, color: COLORS.muted}}>{rule.formula}</div>
      </div>
    </>
  );
};

const EastOrdersScene: React.FC = () => {
  const frame = useCurrentFrame();
  const amountFocus = progress(frame, 42, 56);
  const amountColumn = TABLE.columns[3];
  const numberPrompts = [
    {value: '689？', label: '全部状态', from: 48},
    {value: '440？', label: '只算已支付', from: 56},
    {value: '390？', label: '再扣退款', from: 64},
  ];
  return (
    <AbsoluteFill style={{background: COLORS.navy}}>
      <QuestionHeader />
      <OrderTable frame={frame} revealRows />
      <div
        style={{
          position: 'absolute',
          left: TABLE.x + amountColumn.x + 2,
          top: TABLE.y + 2,
          width: amountColumn.width - 4,
          height: TABLE.headerHeight + ORDERS.length * TABLE.rowHeight - 4,
          boxSizing: 'border-box',
          border: `4px solid ${COLORS.blue}`,
          background: 'rgba(22,116,243,0.07)',
          opacity: amountFocus,
          pointerEvents: 'none',
        }}
      />
      <div
        style={{
          position: 'absolute',
          left: 1392,
          top: 326,
          width: 408,
          display: 'flex',
          flexDirection: 'column',
          gap: 18,
          fontFamily: FONT_FAMILY,
          color: COLORS.surface,
        }}
      >
        {numberPrompts.map((item) => {
          const reveal = progress(frame, item.from, item.from + 5);
          return (
            <div
              key={item.value}
              style={{
                display: 'flex',
                alignItems: 'baseline',
                justifyContent: 'space-between',
                borderBottom: '1px solid rgba(255,255,255,0.18)',
                padding: '0 4px 14px',
                opacity: reveal,
                transform: `translateX(${(1 - reveal) * 12}px)`,
              }}
            >
              <span style={{fontSize: 64, lineHeight: 1, fontWeight: 820}}>{item.value}</span>
              <span style={{fontSize: 24, color: 'rgba(255,255,255,0.62)'}}>{item.label}</span>
            </div>
          );
        })}
      </div>
      <Disclosure />
    </AbsoluteFill>
  );
};

const ThreeRulesScene: React.FC<{duration: number}> = ({duration}) => {
  const frame = useCurrentFrame();
  const stageLength = duration / 3;
  const ruleIndex = Math.min(2, Math.floor(frame / stageLength));
  const stageFrame = frame - ruleIndex * stageLength;
  const rule = RULES[ruleIndex];
  return (
    <AbsoluteFill style={{background: COLORS.navy}}>
      <QuestionHeader compact />
      <OrderTable frame={frame} activeRows={rule.highlightRows} />
      <RuleGeometry rule={rule} stageFrame={stageFrame} />
      <div
        style={{
          position: 'absolute',
          right: 120,
          top: 112,
          color: 'rgba(255,255,255,0.66)',
          fontFamily: FONT_FAMILY,
          fontSize: 28,
          fontWeight: 650,
        }}
      >
        第 {ruleIndex + 1} 种算法
      </div>
      <Disclosure />
    </AbsoluteFill>
  );
};

const PlainLanguageScene: React.FC = () => (
  <AbsoluteFill
    style={{
      background: COLORS.paper,
      color: COLORS.ink,
      fontFamily: FONT_FAMILY,
      justifyContent: 'center',
      alignItems: 'center',
    }}
  >
    <div style={{display: 'flex', gap: 28, marginBottom: 62}}>
      {[
        ['689', '全部状态'],
        ['440', '只算已支付'],
        ['390', '再扣退款'],
      ].map(([value, label]) => (
        <div
          key={value}
          style={{
            minWidth: 210,
            borderBottom: `4px solid ${value === '390' ? COLORS.green : COLORS.blue}`,
            padding: '0 18px 16px',
            textAlign: 'center',
          }}
        >
          <div style={{fontSize: 58, lineHeight: 1, fontWeight: 820}}>{value}</div>
          <div style={{marginTop: 10, fontSize: 24, color: COLORS.muted}}>{label}</div>
        </div>
      ))}
    </div>
    <div style={{fontSize: 82, lineHeight: 1.12, fontWeight: 820, letterSpacing: -3.2}}>
      不是算错，是规则没说清
    </div>
    <div style={{marginTop: 30, fontSize: 34, color: COLORS.muted}}>
      哪些算进去 · 退款怎么处理 · 按哪个时间
    </div>
    <div
      style={{
        position: 'absolute',
        left: 72,
        bottom: 28,
        fontSize: 28,
        color: COLORS.muted,
      }}
    >
      公开合成演示 · 三个数字不是产品运行结果
    </div>
  </AbsoluteFill>
);

const WorkbenchImage: React.FC<{src: string; opacity?: number}> = ({src, opacity = 1}) => (
  <Img
    src={staticFile(src)}
    style={{
      position: 'absolute',
      inset: 0,
      width: WIDTH,
      height: HEIGHT,
      objectFit: 'fill',
      opacity,
    }}
  />
);

const FocusCrop: React.FC<{
  rect: Rect;
  startOpacity: number;
  goalOpacity: number;
  borderColor: string;
  borderWidth: number;
}> = ({rect, startOpacity, goalOpacity, borderColor, borderWidth}) => (
  <div
    style={{
      position: 'absolute',
      left: rect.x,
      top: rect.y,
      width: rect.width,
      height: rect.height,
      borderRadius: rect.radius,
      overflow: 'hidden',
      boxSizing: 'border-box',
      border: `${borderWidth}px solid ${borderColor}`,
    }}
  >
    <Img
      src={staticFile('textures/workbench-start.jpg')}
      style={{
        position: 'absolute',
        left: -rect.x - borderWidth,
        top: -rect.y - borderWidth,
        width: WIDTH,
        height: HEIGHT,
        maxWidth: 'none',
        opacity: startOpacity,
      }}
    />
    <Img
      src={staticFile('textures/workbench-goal.jpg')}
      style={{
        position: 'absolute',
        left: -rect.x - borderWidth,
        top: -rect.y - borderWidth,
        width: WIDTH,
        height: HEIGHT,
        maxWidth: 'none',
        opacity: goalOpacity,
      }}
    />
  </div>
);

const Cursor: React.FC<{x: number; y: number; pressed: number}> = ({x, y, pressed}) => (
  <div
    style={{
      position: 'absolute',
      left: x,
      top: y,
      width: 45,
      height: 58,
      transform: `translate(-7px, -5px) scale(${1 - pressed * 0.08})`,
      transformOrigin: '8px 7px',
      zIndex: 75,
    }}
  >
    <svg width="45" height="58" viewBox="0 0 45 58" aria-hidden="true">
      <path
        d="M5 3 L5 43 L15 34 L23 53 L32 49 L23 30 L37 29 Z"
        fill="#FFFFFF"
        stroke={COLORS.navy}
        strokeWidth="4"
        strokeLinejoin="round"
      />
    </svg>
  </div>
);

function interpolatePoint(
  frame: number,
  points: Array<{frame: number; x: number; y: number}>,
): {x: number; y: number} {
  if (frame <= points[0].frame) return {x: points[0].x, y: points[0].y};
  if (frame >= points[points.length - 1].frame) {
    const point = points[points.length - 1];
    return {x: point.x, y: point.y};
  }
  const rightIndex = points.findIndex((point) => point.frame >= frame);
  const left = points[rightIndex - 1];
  const right = points[rightIndex];
  const local = interpolate(frame, [left.frame, right.frame], [0, 1], {
    ...clampInterpolation,
    easing: easeInOut,
  });
  return {
    x: left.x + (right.x - left.x) * local,
    y: left.y + (right.y - left.y) * local,
  };
}

const WorkbenchEntryScene: React.FC<{duration: number}> = ({duration}) => {
  const frame = useCurrentFrame();
  const inputMoveStart = Math.max(46, duration - 78);
  const goalStateStart = duration - 60;
  const finalHoldStart = duration - 42;
  const goalOpacity = progress(frame, goalStateStart, goalStateStart + 6, easeInOut);
  const dimOpacity = progress(frame, 8, 16) * 0.42;
  const sourceFade = rangeOpacity(frame, 8, 14, inputMoveStart + 4, inputMoveStart + 12);
  const inputFade = progress(frame, inputMoveStart + 2, inputMoveStart + 10);

  const sourceRects = [WORKBENCH_RECTS.orders, WORKBENCH_RECTS.customers, WORKBENCH_RECTS.notes];
  const visitFrames = [14, 27, 40];
  const currentSource =
    frame < visitFrames[1] ? 0 : frame < visitFrames[2] ? 1 : frame < inputMoveStart + 4 ? 2 : -1;
  const inputPoint = {
    x: WORKBENCH_RECTS.goal.x + WORKBENCH_RECTS.goal.width * 0.2,
    y: WORKBENCH_RECTS.goal.y + WORKBENCH_RECTS.goal.height * 0.55,
  };
  const cursorPoint = interpolatePoint(frame, [
    {frame: 0, x: 560, y: 604},
    {
      frame: visitFrames[0],
      x: WORKBENCH_RECTS.orders.x + WORKBENCH_RECTS.orders.width * 0.52,
      y: WORKBENCH_RECTS.orders.y + WORKBENCH_RECTS.orders.height * 0.55,
    },
    {
      frame: visitFrames[1],
      x: WORKBENCH_RECTS.customers.x + WORKBENCH_RECTS.customers.width * 0.52,
      y: WORKBENCH_RECTS.customers.y + WORKBENCH_RECTS.customers.height * 0.55,
    },
    {
      frame: visitFrames[2],
      x: WORKBENCH_RECTS.notes.x + WORKBENCH_RECTS.notes.width * 0.52,
      y: WORKBENCH_RECTS.notes.y + WORKBENCH_RECTS.notes.height * 0.55,
    },
    {frame: goalStateStart - 9, x: inputPoint.x, y: inputPoint.y},
    {frame: duration, x: inputPoint.x, y: inputPoint.y},
  ]);
  // Source rows are shown as the already-scoped material in the real capture; only
  // the goal field receives a press state so the animation does not invent file clicks.
  const pressMoments = [goalStateStart - 8];
  const pressed = Math.max(
    ...pressMoments.map((moment) =>
      interpolate(Math.abs(frame - moment), [0, 3, 6], [1, 0.5, 0], clampInterpolation),
    ),
  );

  return (
    <AbsoluteFill style={{background: COLORS.surface}}>
      <WorkbenchImage src="textures/workbench-start.jpg" opacity={1 - goalOpacity} />
      <WorkbenchImage src="textures/workbench-goal.jpg" opacity={goalOpacity} />
      <div
        style={{
          position: 'absolute',
          inset: 0,
          background: `rgba(13,27,46,${dimOpacity})`,
        }}
      />
      {sourceRects.map((rect, index) => (
        <div key={index} style={{opacity: sourceFade}}>
          <FocusCrop
            rect={rect}
            startOpacity={1 - goalOpacity}
            goalOpacity={goalOpacity}
            borderColor={index === currentSource ? COLORS.blue : 'rgba(22,116,243,0.62)'}
            borderWidth={index === currentSource ? 4 : 2}
          />
        </div>
      ))}
      <div style={{opacity: inputFade}}>
        <FocusCrop
          rect={WORKBENCH_RECTS.goal}
          startOpacity={1 - goalOpacity}
          goalOpacity={goalOpacity}
          borderColor={COLORS.blue}
          borderWidth={4}
        />
      </div>
      <Cursor x={cursorPoint.x} y={cursorPoint.y} pressed={pressed} />
      <div
        style={{
          position: 'absolute',
          top: 54,
          left: '50%',
          transform: 'translateX(-50%)',
          borderRadius: 10,
          background: 'rgba(13,27,46,0.94)',
          color: COLORS.surface,
          padding: '16px 30px 18px',
          fontFamily: FONT_FAMILY,
          fontSize: 54,
          lineHeight: 1,
          fontWeight: 780,
          letterSpacing: -1.5,
        }}
      >
        确认资料，说目标
      </div>
      <div
        style={{
          position: 'absolute',
          right: 72,
          bottom: 30,
          fontFamily: FONT_FAMILY,
          color: COLORS.navy,
          background: 'rgba(255,255,255,0.94)',
          border: `1px solid ${COLORS.line}`,
          borderRadius: 8,
          padding: '8px 12px',
          fontSize: 25,
          opacity: progress(frame, finalHoldStart - 6, finalHoldStart),
        }}
      >
        目标已输入，等待发送
      </div>
      <Disclosure onWorkbench />
    </AbsoluteFill>
  );
};

function musicVolume(frame: number, profile: HookProfile): number {
  const quiet = profile.scenes.plainLanguage;
  const duckIn = interpolate(frame, [quiet.from - 5, quiet.from + 2], [1, 0.36], clampInterpolation);
  const duckOut = interpolate(
    frame,
    [quiet.from + quiet.duration - 2, profile.scenes.workbenchEntry.from + 8],
    [0.36, 1],
    clampInterpolation,
  );
  const quietGain = frame < quiet.from + quiet.duration / 2 ? duckIn : duckOut;
  const endFade = interpolate(frame, [344, 359], [1, 0], clampInterpolation);
  return profile.musicGain * quietGain * endFade;
}

const HookAudio: React.FC<{variant: HookVariant; profile: HookProfile}> = ({variant, profile}) => {
  const stageLength = profile.scenes.threeRules.duration / 3;
  const markerFrames = [0, 1, 2].map(
    (index) => profile.scenes.threeRules.from + Math.round(stageLength * index) + 1,
  );
  return (
    <>
      <Audio
        src={staticFile(profile.music)}
        trimBefore={profile.musicStartFrame}
        volume={(frame) => musicVolume(frame, profile)}
      />
      <Sequence from={7} durationInFrames={13}>
        <Audio src={staticFile('audio/v4/paper-slice-quick.mp3')} volume={0.13} />
      </Sequence>
      {markerFrames.map((from) => (
        <Sequence key={from} from={from} durationInFrames={14}>
          <Audio src={staticFile('audio/v4/marker-pen-line.mp3')} volume={variant === 'A' ? 0.11 : 0.13} />
        </Sequence>
      ))}
      <Sequence from={profile.scenes.workbenchEntry.from + 2} durationInFrames={13}>
        <Audio src={staticFile('audio/v4/paper-slice-quick.mp3')} volume={0.1} />
      </Sequence>
    </>
  );
};

const ProductFilmV4Hook: React.FC<{variant: HookVariant}> = ({variant}) => {
  const profile = PROFILES[variant];
  return (
    <AbsoluteFill style={{background: COLORS.navy}}>
      <Sequence from={profile.scenes.eastOrders.from} durationInFrames={profile.scenes.eastOrders.duration}>
        <EastOrdersScene />
      </Sequence>
      <Sequence from={profile.scenes.threeRules.from} durationInFrames={profile.scenes.threeRules.duration}>
        <ThreeRulesScene duration={profile.scenes.threeRules.duration} />
      </Sequence>
      <Sequence from={profile.scenes.plainLanguage.from} durationInFrames={profile.scenes.plainLanguage.duration}>
        <PlainLanguageScene />
      </Sequence>
      <Sequence
        from={profile.scenes.workbenchEntry.from}
        durationInFrames={profile.scenes.workbenchEntry.duration}
      >
        <WorkbenchEntryScene duration={profile.scenes.workbenchEntry.duration} />
      </Sequence>
      <HookAudio variant={variant} profile={profile} />
    </AbsoluteFill>
  );
};

export const ContextOxProductFilmV4HookA: React.FC = () => <ProductFilmV4Hook variant="A" />;

export const ContextOxProductFilmV4HookB: React.FC = () => <ProductFilmV4Hook variant="B" />;
