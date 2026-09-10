import { useState } from "react";
import { exportCandidate } from "./api/client";
import type { DefinitionDraft } from "./Path2Workbench";
import { errorMessage } from "./WorkspaceSwitcher";

export function CandidateExport({ draft }: { draft: DefinitionDraft | null }) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const download = async (format: "markdown" | "json") => {
    if (!draft || pending) return;
    setPending(true); setError("");
    try {
      const result = await exportCandidate(draft.workspace_id, draft.mission_id, draft.version, draft.sha256);
      const blob = new Blob([format === "markdown" ? result.markdown : JSON.stringify(result.candidate, null, 2)],
        { type: format === "markdown" ? "text/markdown;charset=utf-8" : "application/json;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url; link.download = `contextox-candidate-v${result.candidate.draft.version}.${format === "markdown" ? "md" : "json"}`;
      link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) { setError(errorMessage(e)); }
    finally { setPending(false); }
  };
  if (!draft) return null;
  return <div className="candidate-export"><span>导出候选 v{draft.version}</span>
    <button type="button" className="utility-button" disabled={pending} onClick={() => void download("markdown")}>Markdown ↓</button>
    <button type="button" className="utility-button" disabled={pending} onClick={() => void download("json")}>JSON ↓</button>
    {pending && <span role="status">核对当前版本…</span>}{error && <p role="alert">{error}</p>}
  </div>;
}
