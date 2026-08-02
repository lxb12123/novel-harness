import { useEffect, useState } from "react";
import { ApiError } from "../api/client";
import { useAiSettings, useSaveAiSettings } from "../api/hooks";
import type { AiSettingsInput } from "../api/types";

// AI 设置（BYOK）——像 Cursor 的 API Keys：作者粘一把自己的钥匙，存在本机。
// 钥匙永不回显：后端只给「设没设 + 后四位」；留空提交 = 保持原钥匙。
export function SettingsDrawer({ onClose }: { onClose: () => void }) {
  const settings = useAiSettings();
  const save = useSaveAiSettings();
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [apiKey, setApiKey] = useState("");

  useEffect(() => {
    if (settings.data) {
      setBaseUrl(settings.data.base_url);
      setModel(settings.data.model);
    }
  }, [settings.data]);

  const err = save.error instanceof ApiError ? save.error : null;
  const current = settings.data;
  const dirty =
    baseUrl.trim() !== (current?.base_url ?? "") ||
    model.trim() !== (current?.model ?? "") ||
    apiKey.trim() !== "";

  function onSubmit() {
    const input: AiSettingsInput = {
      base_url: baseUrl.trim(),
      model: model.trim(),
    };
    if (apiKey.trim()) input.api_key = apiKey.trim();
    save.mutate(input, { onSuccess: onClose });
  }

  const card = (
    <div className="setup-card">
      <h3>AI 设置</h3>
      <div className="field">
        <span>服务地址</span>
        <input
          value={baseUrl}
          placeholder="https://api.deepseek.com"
          onChange={(e) => setBaseUrl(e.target.value)}
        />
      </div>
      <div className="field">
        <span>模型</span>
        <input
          value={model}
          placeholder="deepseek-v4-flash"
          onChange={(e) => setModel(e.target.value)}
        />
      </div>
      <div className="field">
        <span>API 钥匙</span>
        <input
          type="password"
          value={apiKey}
          placeholder={
            current?.api_key_set
              ? `已设置（${current.api_key_preview}），留空 = 不换`
              : "粘贴你的 sk-…"
          }
          onChange={(e) => setApiKey(e.target.value)}
        />
        {current?.api_key_set && (
          <div className="note">钥匙只存在这台电脑上，不会回显。</div>
        )}
      </div>
      {err && <div className="err-box">{err.message}</div>}
      <div className="row" style={{ marginTop: 10 }}>
        <button onClick={onClose}>关闭</button>
        <button disabled={!dirty || save.isPending} onClick={onSubmit}>
          {save.isPending ? "保存中…" : "保存"}
        </button>
      </div>
      <div className="note">
        起草能力在 M2 通过后开放；钥匙用于调用你填的模型服务。
      </div>
    </div>
  );

  return (
    <>
      <div className="backdrop" onClick={onClose} />
      <div className="drawer">{card}</div>
    </>
  );
}
