import { useEffect, useState } from 'react';
import {
  getLLMSettings,
  saveLLMSettings,
  testLLMConnection,
  type LLMCompatInfo,
} from '../api';

function formatCompat(c: LLMCompatInfo | null | undefined, valid?: boolean): string {
  if (!valid || !c) {
    return '网关格式未探测或已过期，请点击测试';
  }
  const chat = c.chat_ok ? '聊天 ✓' : '聊天 ✗';
  if (!c.tools_supported || c.tool_name_style === 'none') {
    return `${chat} · 不支持工具 ✗（仅聊天可用）`;
  }
  return `${chat} · 工具名风格：${c.tool_name_style}`;
}

export function LLMSettingsCard() {
  const [baseUrl, setBaseUrl] = useState('');
  const [model, setModel] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [status, setStatus] = useState<string | null>(null);
  const [compatHint, setCompatHint] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    getLLMSettings()
      .then((cfg) => {
        setBaseUrl(cfg.base_url);
        setModel(cfg.model);
        setCompatHint(formatCompat(cfg.compat, cfg.compat_valid));
      })
      .catch((e) => setStatus(String(e)));
  }, []);

  const input: React.CSSProperties = {
    padding: '6px 10px',
    border: '1px solid #ccc',
    borderRadius: 4,
    minWidth: 220,
  };

  const doSave = async () => {
    setBusy(true);
    try {
      const saved = await saveLLMSettings({
        base_url: baseUrl,
        model,
        api_key: apiKey,
      });
      setStatus('已保存');
      setCompatHint(formatCompat(saved.compat, saved.compat_valid));
      setApiKey('');
    } catch (e) {
      setStatus(String(e));
    } finally {
      setBusy(false);
    }
  };

  const doTest = async () => {
    setBusy(true);
    setStatus('测试中（含工具名探测）…');
    try {
      const r = await testLLMConnection({
        base_url: baseUrl,
        model,
        api_key: apiKey,
      });
      if (!r.ok) {
        setStatus(`失败：${r.error}`);
        return;
      }
      setCompatHint(formatCompat(r.compat, r.compat_valid));
      const style = r.compat?.tool_name_style ?? 'none';
      const tools = r.compat?.tools_supported
        ? `工具名风格 ${style}`
        : '不支持工具';
      setStatus(`连通正常：${r.sample ?? 'ok'} · ${tools}`);
    } catch (e) {
      setStatus(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section
      data-testid="llm-settings"
      style={{
        background: 'var(--ui-bg-elevated)',
        border: '1px solid var(--ui-stroke-muted)',
        borderRadius: 4,
        padding: 16,
        marginBottom: 0,
        textAlign: 'left',
      }}
    >
      <h3 style={{ marginTop: 0 }}>LLM API 配置</h3>
      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'center' }}>
        <input
          style={input}
          placeholder="Base URL，如 http://127.0.0.1:10151 或 https://api.openai.com/v1"
          value={baseUrl}
          onChange={(e) => setBaseUrl(e.target.value)}
        />
        <input
          style={input}
          placeholder="模型名，如 Qwen2.5-7B-Instruct"
          value={model}
          onChange={(e) => setModel(e.target.value)}
        />
        <input
          style={input}
          type="password"
          placeholder="API Key（留空保持已保存值）"
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
        />
        <button onClick={doSave} disabled={busy || !baseUrl || !model}>
          保存
        </button>
        <button onClick={doTest} disabled={busy || !baseUrl || !model}>
          测试连通
        </button>
      </div>
      {compatHint && (
        <p
          data-testid="llm-compat-hint"
          style={{
            color: compatHint.includes('过期') || compatHint.includes('未探测')
              ? '#a61b1b'
              : '#555',
            fontSize: 13,
            marginTop: 10,
            marginBottom: 0,
          }}
        >
          {compatHint}
        </p>
      )}
      {status && (
        <p style={{ color: '#555', fontSize: 13, marginBottom: 0 }}>{status}</p>
      )}
    </section>
  );
}
