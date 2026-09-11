import { useRef, useState } from "react";
import { createWorkspace, fetchDemo, fetchSources, uploadSources, type DemoCase, type Workspace } from "./api/client";
import { errorMessage, needsCreateReconciliation, writeSelectedWorkspaceId } from "./WorkspaceSwitcher";

export function demoText(encoded: string): string {
  return new TextDecoder().decode(Uint8Array.from(atob(encoded), character => character.charCodeAt(0)));
}

export function DemoEntry({ onLoaded, idPrefix = "demo", buttonLabel = "体验示例", buttonClassName = "utility-button" }: {
  onLoaded: (workspace: Workspace, task: string, revisions: string[]) => void;
  idPrefix?: string;
  buttonLabel?: string;
  buttonClassName?: string;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [example, setExample] = useState<DemoCase | null>(null);
  const [pending, setPending] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [unknown, setUnknown] = useState(false);
  const [source, setSource] = useState<string | null>(null);
  const open = async () => {
    dialog.current?.showModal();
    if (example) return;
    setLoading(true); setError("");
    try { setExample(await fetchDemo()); }
    catch (e) { setError(errorMessage(e)); }
    finally { setLoading(false); }
  };
  const load = async () => {
    if (!example || pending || unknown) return;
    setPending(true); setError("");
    let workspace: Workspace | null = null;
    try {
      workspace = await createWorkspace(`示例 · 订单口径 · ${new Date().toLocaleString()}`);
      await uploadSources(workspace.workspace_id, { local_read_confirmed: true,
        files: example.files.map(({ original_name, media_type, content_base64 }) => ({ original_name, media_type, content_base64 })) });
      const sources = await fetchSources(workspace.workspace_id);
      const selected = example.files.map(file => sources.find(revision => revision.original_name === file.original_name && revision.sha256 === file.sha256 && revision.parse_status === "ready"));
      if (selected.some(item => !item)) throw new Error("部分资料未载入，请在新工作区的资料来源中检查；没有发起模型请求。");
      writeSelectedWorkspaceId(workspace.workspace_id);
      onLoaded(workspace, example.request, selected.map(item => item!.revision_id));
      dialog.current?.close();
    } catch (e) {
      setError(errorMessage(e));
      if (workspace) {
        writeSelectedWorkspaceId(workspace.workspace_id);
        onLoaded(workspace, example.request, []);
      } else if (needsCreateReconciliation(e)) {
        setUnknown(true);
        setError("创建结果暂未确认，请关闭此窗口，在工作区列表核对后再操作。没有自动重新创建。");
      }
    } finally { setPending(false); }
  };
  const selectedFile = example?.files.find(file => file.original_name === source);
  const titleId = `${idPrefix}-title`;
  return <>
    <button type="button" className={buttonClassName} onClick={() => void open()}>{buttonLabel}</button>
    <dialog ref={dialog} className="workbench-dialog demo-dialog" aria-labelledby={titleId} onCancel={e => { if (pending) e.preventDefault(); }}>
      <header><h2 id={titleId}>从一个订单口径任务开始</h2><button type="button" className="utility-button" disabled={pending} aria-label="关闭示例" onClick={() => dialog.current?.close()}>关闭</button></header>
      <p className="demo-disclosure">预制只读示例 · 人工编写的合成材料与候选，用于说明产品流程；不是本次模型生成或批准记录。</p>
      {loading && <p role="status">读取公开示例…</p>}
      {error && <p role="alert" className="settings-error">{error}</p>}
      {!example && !loading && <button className="utility-button" onClick={() => void open()}>重新读取</button>}
      {example && <>
        <h3>{example.title}</h3>
        <p>两张小表 + 一份说明 → 字段与关系候选 → 回答并确认 → 同一任务更新草案。</p>
        <div className="demo-files">{example.files.map(file => <button type="button" className="utility-button" key={file.original_name} aria-pressed={source === file.original_name} onClick={() => setSource(source === file.original_name ? null : file.original_name)}>@{file.original_name}</button>)}</div>
        {selectedFile && <section className="demo-source" aria-label={`示例来源 ${selectedFile.original_name}`}><h4>{selectedFile.original_name}</h4><pre>{demoText(selectedFile.content_base64)}</pre></section>}
        <div className="demo-table-wrap"><table><thead><tr><th>字段候选</th><th>已经知道 / 仍待明确</th><th>来源</th></tr></thead><tbody>{example.fields.map(field => <tr key={field.name}><td>{field.name}</td><td>{field.meaning}</td><td><button type="button" className="demo-citation" title={field.location} onClick={() => setSource(field.file)}>@{field.file}</button><small>{field.location}</small></td></tr>)}</tbody></table></div>
        <h4>表关系</h4><p>{example.relationship}</p>
        <h4>接下来需要你回答</h4><ol>{example.questions.map(question => <li key={question}>{question}</li>)}</ol>
        <details><summary>保留在草案中的其他未知项</summary><ul>{example.remaining_unknowns.map(item => <li key={item}>{item}</li>)}</ul></details>
        <details><summary>亲自运行时使用的任务描述</summary><p>{example.request}</p></details>
        <footer><p>载入只会创建一个新的本地工作区。配置 Key 并确认发送后，才产生 API 费用。</p><button type="button" className="path2-primary-button" disabled={pending || unknown} onClick={() => void load()}>{pending ? "正在载入本地资料…" : "载入示例，亲自运行"}</button></footer>
        {unknown && <button type="button" className="utility-button" onClick={() => {setUnknown(false); setError("");}}>我已核对工作区列表，允许创建另一份示例</button>}
      </>}
    </dialog>
  </>;
}
