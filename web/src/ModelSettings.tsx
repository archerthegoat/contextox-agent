import { useEffect, useRef, useState } from "react";
import { fetchDeepSeekSettings, removeDeepSeekKey, saveDeepSeekKey, type DeepSeekSettings } from "./api/client";
import { errorMessage } from "./WorkspaceSwitcher";

export function ModelSettings() {
  const dialog = useRef<HTMLDialogElement>(null);
  const [settings, setSettings] = useState<DeepSeekSettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [pending, setPending] = useState(false);
  const [key, setKey] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const refresh = async () => {
    setLoading(true); setError("");
    try { setSettings(await fetchDeepSeekSettings()); }
    catch (e) { setError(errorMessage(e)); }
    finally { setLoading(false); }
  };
  useEffect(() => { void refresh(); }, []);
  const changeKey = async (remove: boolean) => {
    if (!settings || pending) return;
    const submittedKey = key;
    setKey(""); setPending(true); setError(""); setNotice("");
    try {
      const result = remove ? await removeDeepSeekKey(settings.session_token)
        : await saveDeepSeekKey(submittedKey, settings.session_token);
      setSettings(result);
      setNotice(remove ? "已移除本机保存的 Key。" : "Key 已保存到本机 Keychain。创建任务并确认发送后，才会调用模型。");
    } catch (e) { setError(errorMessage(e)); }
    finally { setPending(false); }
  };
  const external = settings?.source === "environment" || settings?.source === "env_file";
  const unavailable = loading || pending || !settings || settings.busy || external;
  return <>
    <button type="button" className="utility-button" onClick={() => { dialog.current?.showModal(); void refresh(); }}>
      {settings?.configured ? "模型设置" : "配置模型"}
    </button>
    <dialog ref={dialog} className="workbench-dialog" aria-labelledby="model-settings-title"
      onCancel={e => { if (pending) e.preventDefault(); }} onClose={() => { setKey(""); setNotice(""); }}>
      <header><h2 id="model-settings-title">连接 DeepSeek</h2><button type="button" className="utility-button" disabled={pending} aria-label="关闭模型设置" onClick={() => dialog.current?.close()}>关闭</button></header>
      <p>填入你自己的 DeepSeek API Key。任务资料只在你确认发送后交给 DeepSeek，费用由你的 API 账户承担。</p>
      {loading ? <p role="status">读取本机配置…</p> : settings && <>
        <p className="settings-status">{({environment:"已配置 · 由启动环境管理",env_file:"已配置 · 来自显式本地配置文件",keychain:"已配置 · 保存在 macOS Keychain",missing:"尚未配置 Key",unavailable:"暂时无法访问 Keychain"})[settings.source]}</p>
        <p className="settings-detail">DeepSeek Flash · {settings.thinking === "disabled" ? "Demo 快速模式（非思考）" : "生产模式（high）"}。保存状态不代表已验证模型连接。</p>
        {external && <p>读取顺序为环境变量、--env-file 指定文件、Keychain。请修改对应配置后重启服务；网页不会改写外部配置。</p>}
        {settings.busy && <p role="status">有任务正在执行，结束后点击“刷新状态”再修改 Key。</p>}
        {settings.source === "unavailable" && <p>请解锁 macOS 登录钥匙串后重试。Key 不会保存到普通文件。</p>}
        <form onSubmit={e => { e.preventDefault(); void changeKey(false); }}>
          <label htmlFor="deepseek-api-key">DeepSeek API Key</label>
          <input id="deepseek-api-key" type="password" autoComplete="off" spellCheck={false} minLength={16} maxLength={512}
            required value={key} disabled={unavailable} onChange={e => setKey(e.target.value)} placeholder={settings.configured ? "输入新 Key 以替换" : "粘贴 API Key"} />
          <div className="dialog-actions"><button type="submit" className="primary-button" disabled={unavailable || key.length < 16}>{pending ? "保存中…" : "保存到 Keychain"}</button>
            {settings.source === "keychain" && <button type="button" className="utility-button" disabled={unavailable} onClick={() => void changeKey(true)}>移除 Key</button>}
          </div>
        </form>
      </>}
      {error && <p role="alert" className="settings-error">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      <p className="settings-detail">也可在启动环境设置 DEEPSEEK_API_KEY，或使用 start --env-file 指定自己的本地配置文件。</p>
      <footer><a href="https://platform.deepseek.com/api_keys" target="_blank" rel="noreferrer">获取 DeepSeek API Key ↗</a><button type="button" className="utility-button" disabled={pending || loading} onClick={() => void refresh()}>刷新状态</button></footer>
    </dialog>
  </>;
}
