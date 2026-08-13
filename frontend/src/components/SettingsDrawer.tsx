import { useEffect, useState } from "react";
import { ApiError } from "../api/client";
import {
  useAiSettings,
  useRefreshModelWindows,
  useSaveAiSettings,
} from "../api/hooks";
import type { AiSettingsInput } from "../api/types";

// AI 设置（BYOK）——像 Cursor 的 API Keys：作者粘一把自己的钥匙，存在本机。
// 钥匙永不回显：后端只给「设没设 + 后四位」；留空提交 = 保持原钥匙。
export function SettingsDrawer({ onClose }: { onClose: () => void }) {
  const settings = useAiSettings();
  const save = useSaveAiSettings();
  const refresh = useRefreshModelWindows();
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
  const refreshErr = refresh.error instanceof ApiError ? refresh.error : null;
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
        <span>模型名称</span>
        <input
          value={model}
          placeholder="deepseek-v4-flash"
          onChange={(e) => setModel(e.target.value)}
        />
      </div>
      <div className="field">
        <span>API 密钥</span>
        <input
          type="password"
          value={apiKey}
          placeholder={
            current?.api_key_set
              ? `已设置（${current.api_key_preview}），留空则保留`
              : "粘贴 API 密钥"
          }
          onChange={(e) => setApiKey(e.target.value)}
        />
        {current?.api_key_set && (
          <div className="note">为保护安全，不会显示完整密钥。</div>
        )}
      </div>
      {err && <div className="err-box">{err.message}</div>}

      {/* ── 模型信息 ────────────────────────────────────────────────────
          这份表决定**上文给作者多长**（同一个模型，认得出是上万字，认不出是 800）。
          它来自一个我们不控制的公开仓库，所以**只在这儿点，绝不自动跑** ——
          自动更新等于别人改一行、作者明天的稿子上下文就变了，而他不知道为什么。 */}
      <div className="field">
        <span>模型信息</span>
        <div className="row">
          <button disabled={refresh.isPending} onClick={() => refresh.mutate()}>
            {refresh.isPending ? "正在更新…" : "更新模型信息"}
          </button>
        </div>
        {refreshErr ? (
          <div className="err-box">{refreshErr.message}</div>
        ) : refresh.data ? (
          <div className="note">
            已更新到 {refresh.data.fetched}：认得 {refresh.data.total} 个模型
            {refresh.data.added || refresh.data.changed || refresh.data.removed
              ? `（新增 ${refresh.data.added}、变化 ${refresh.data.changed}、减少 ${refresh.data.removed}）`
              : "，和原来那份一样"}
            。
          </div>
        ) : (
          <div className="note">
            用来知道你选的模型能记住多长的上文。不更新也能用，只是新出的模型可能认不出。
          </div>
        )}
      </div>

      <div className="row" style={{ marginTop: 10 }}>
        <button onClick={onClose}>关闭</button>
        <button disabled={!dirty || save.isPending} onClick={onSubmit}>
          {save.isPending ? "保存中…" : "保存"}
        </button>
      </div>
      <div className="note">
        这些设置只保存在这台电脑上，用于连接你选择的模型服务。
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
