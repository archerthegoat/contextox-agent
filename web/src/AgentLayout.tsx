import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";

export const AGENT_WIDTH_KEY = "contextox.agent_panel_width";
export function agentWidthBounds(available: number) {
  return { min: 360, max: Math.max(360, available - 360), initial: Math.min(640, Math.max(360, available * .44)) };
}
export function clampAgentWidth(width: number, available: number) {
  const bounds = agentWidthBounds(available);
  return Math.round(Math.max(bounds.min, Math.min(bounds.max, Number.isFinite(width) ? width : bounds.initial)));
}

export function AgentLayout({sidebar, center, agent, expanded, mobileView, onMobileView, navOpen, onNavOpen}: {
  sidebar: ReactNode; center: ReactNode; agent: ReactNode; expanded: boolean;
  mobileView: "result" | "agent"; onMobileView: (view: "result" | "agent") => void;
  navOpen: boolean; onNavOpen: (open: boolean) => void;
}) {
  const layout = useRef<HTMLDivElement>(null);
  const separator = useRef<HTMLDivElement>(null);
  const [available, setAvailable] = useState(1000);
  const [preference, setPreference] = useState<number | null>(() => {
    try {const value = Number(localStorage.getItem(AGENT_WIDTH_KEY)); return value >= 360 ? value : null;} catch {return null;}
  });
  useEffect(() => {
    const element = layout.current;
    if (!element) return;
    const measure = () => {
      const nav = element.querySelector<HTMLElement>(".agent-led-navigation");
      setAvailable(Math.max(0, element.clientWidth - (nav?.offsetWidth ?? 220) - 7));
    };
    measure();
    const observer = new ResizeObserver(measure); observer.observe(element);
    return () => observer.disconnect();
  }, []);
  const bounds = agentWidthBounds(available);
  const width = clampAgentWidth(preference ?? bounds.initial, available);
  const update = (value: number | null) => {
    const next = value === null ? null : clampAgentWidth(value, available); setPreference(next);
    try {if (next === null) localStorage.removeItem(AGENT_WIDTH_KEY); else localStorage.setItem(AGENT_WIDTH_KEY, String(next));} catch {/* Width still works in this page. */}
  };
  return <>
    <div className="compact-controls"><button aria-expanded={navOpen} onClick={() => onNavOpen(!navOpen)}>导航</button><strong>数契</strong><div><button aria-pressed={mobileView === "agent"} onClick={() => onMobileView("agent")}>对话</button><button aria-pressed={mobileView === "result"} onClick={() => onMobileView("result")}>工作区</button></div></div>
    <div ref={layout} style={{"--agent-width": `${width}px`} as CSSProperties} className={`workspace-layout agent-led-layout mobile-${mobileView}${navOpen ? " nav-open" : ""}${expanded ? " agent-expanded" : ""}`}>
      {sidebar}{center}
      <div ref={separator} className="agent-width-separator" role="separator" tabIndex={0} aria-label="调整 Agent 栏宽度" aria-orientation="vertical" aria-valuemin={bounds.min} aria-valuemax={bounds.max} aria-valuenow={width} aria-controls="agent-panel-content"
        onPointerDown={event => {if(event.button === 0) event.currentTarget.setPointerCapture(event.pointerId);}}
        onPointerMove={event => {if(event.currentTarget.hasPointerCapture(event.pointerId)) update((layout.current?.getBoundingClientRect().right ?? innerWidth) - event.clientX);}}
        onPointerUp={event => {if(event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);}}
        onPointerCancel={event => {if(event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);}}
        onDoubleClick={() => update(null)} onKeyDown={event => {if(event.key === "ArrowLeft" || event.key === "ArrowRight") {event.preventDefault();update(width + (event.key === "ArrowLeft" ? 24 : -24));} else if(event.key === "Home") {event.preventDefault();update(null);}}}><span /></div>
      {agent}
    </div>
  </>;
}
