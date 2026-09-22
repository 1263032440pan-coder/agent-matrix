import { useEffect, useState } from 'react';
import { getLLMSettings, saveLLMSettings, testLLMConnection } from '../api';

export function LLMSettingsCard() {
  const [baseUrl, setBaseUrl] = useState('');
  const [model, setModel] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    getLLMSettings()
      .then((cfg) => {
        setBaseUrl(cfg.base_url);
        setModel(cfg.model);
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
      await saveLLMSettings({ base_url: baseUrl, model, api_key: apiKey });
      setStatus('已保存');
      setApiKey('');
    } catch (e) {
      setStatus(String(e));
    } finally {
      setBusy(false);
    }
  };

  const doTest = async () => {
    setBusy(true);
    setStatus('测试中…');
    try {
      const r = await testLLMConnection({ base_url: baseUrl, model, api_key: apiKey });
      setStatus(r.ok ? `连通正常：${r.sample ?? 'ok'}` : `失败：${r.error}`);
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
        background: '#fff',
        border: '1px solid #e5e7eb',
        borderRadius: 8,
        padding: 16,
        marginBottom: 16,
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
      {status && <p style={{ color: '#555', fontSize: 13, marginBottom: 0 }}>{status}</p>}
    </section>
  );
}