import { Activity, AlertTriangle, CheckCircle2, Loader2, Settings, X } from 'lucide-react'
import { useEffect, useState } from 'react'
import { testLlmConfig, updateLlmConfig } from '../api'
import type { LlmConfig, LlmProvider } from '../types'

interface ModelSettingsProps {
  config: LlmConfig | null
  onClose: () => void
  onSaved: (config: LlmConfig) => void
}

interface ProviderDraft {
  api_key: string
  base_url: string
  model: string
}

const PROVIDER_LABELS: Record<LlmProvider, string> = {
  deepseek: 'DeepSeek',
  ark: '火山引擎',
  minimax: 'MiniMax',
  openai: 'OpenAI',
}

const ALL_PROVIDERS: LlmProvider[] = ['deepseek', 'ark', 'minimax', 'openai']

function createDraft(config: LlmConfig | null): Record<LlmProvider, ProviderDraft> {
  return {
    deepseek: {
      api_key: '',
      base_url: config?.deepseek.base_url ?? 'https://api.deepseek.com',
      model: config?.deepseek.model ?? 'deepseek-chat',
    },
    openai: {
      api_key: '',
      base_url: config?.openai.base_url ?? 'https://api.openai.com/v1',
      model: config?.openai.model ?? 'gpt-4o-mini',
    },
    ark: {
      api_key: '',
      base_url: config?.ark.base_url ?? 'https://ark.cn-beijing.volces.com/api/v3',
      model: config?.ark.model ?? 'doubao-seed-2-1-pro-260628',
    },
    minimax: {
      api_key: '',
      base_url: config?.minimax.base_url ?? 'https://api.minimaxi.com/v1',
      model: config?.minimax.model ?? 'MiniMax-M2',
    },
  }
}

export function ModelSettings({ config, onClose, onSaved }: ModelSettingsProps) {
  const [provider, setProvider] = useState<LlmProvider>(config?.provider ?? 'deepseek')
  const [draft, setDraft] = useState(() => createDraft(config))
  const [busy, setBusy] = useState(false)
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState('')
  const [testError, setTestError] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [onClose])

  const selected = config?.[provider]

  const updateDraft = (field: keyof ProviderDraft, value: string) => {
    setDraft((current) => ({
      ...current,
      [provider]: {
        ...current[provider],
        [field]: value,
      },
    }))
  }

  const resetTestState = () => {
    setTestResult('')
    setTestError('')
  }

  const submit = async () => {
    if (busy) return
    setBusy(true)
    setError('')
    try {
      const next = await updateLlmConfig({
        provider,
        deepseek_api_key: draft.deepseek.api_key.trim(),
        deepseek_base_url: draft.deepseek.base_url.trim(),
        deepseek_model: draft.deepseek.model.trim(),
      openai_api_key: draft.openai.api_key.trim(),
      openai_base_url: draft.openai.base_url.trim(),
      openai_model: draft.openai.model.trim(),
      ark_api_key: draft.ark.api_key.trim(),
      ark_base_url: draft.ark.base_url.trim(),
      ark_model: draft.ark.model.trim(),
      minimax_api_key: draft.minimax.api_key.trim(),
      minimax_base_url: draft.minimax.base_url.trim(),
      minimax_model: draft.minimax.model.trim(),
      })
      onSaved(next)
      onClose()
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : '保存模型配置失败。')
    } finally {
      setBusy(false)
    }
  }

  const testConnection = async () => {
    if (testing) return
    setTesting(true)
    resetTestState()
    try {
      const result = await testLlmConfig({
        provider,
        api_key: draft[provider].api_key.trim(),
        base_url: draft[provider].base_url.trim(),
        model: draft[provider].model.trim(),
      })
      setTestResult(`${result.message}（${result.latency_ms}ms · ${result.model}）`)
    } catch (connectionError) {
      setTestError(connectionError instanceof Error ? connectionError.message : '连通性测试失败。')
    } finally {
      setTesting(false)
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      role="dialog"
      aria-modal="true"
      aria-label="模型配置"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose()
      }}
    >
      <section className="w-full max-w-2xl rounded-lg border border-zinc-200 bg-white p-5 shadow-xl">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 className="flex items-center gap-2 text-lg font-bold text-zinc-900">
              <Settings className="size-5 text-emerald-600" aria-hidden="true" />
              模型配置
            </h2>
            <p className="mt-1 text-xs leading-5 text-zinc-500">
              API Key 只保存在本机 backend/.env；界面刷新后只显示掩码。
            </p>
          </div>
          <button
            type="button"
            className="rounded-lg p-2 text-zinc-400 transition hover:bg-zinc-100 hover:text-zinc-700"
            onClick={onClose}
            aria-label="关闭模型配置"
          >
            <X className="size-4" aria-hidden="true" />
          </button>
        </div>

        <div className="mt-4 grid grid-cols-2 gap-1 rounded-lg bg-zinc-100 p-1 md:grid-cols-4">
          {ALL_PROVIDERS.map((item) => (
            <button
              key={item}
              type="button"
              className={`inline-flex flex-1 items-center justify-center gap-2 rounded-md px-3 py-2 text-sm font-semibold transition ${
                provider === item
                  ? 'bg-white text-zinc-900 shadow-sm'
                  : 'text-zinc-500 hover:text-zinc-700'
              }`}
              onClick={() => {
                setProvider(item)
                resetTestState()
              }}
            >
              <span
                className={`size-2 rounded-full ${
                  config?.[item]?.configured ? 'bg-emerald-500' : 'bg-amber-400'
                }`}
                aria-hidden="true"
              />
              {PROVIDER_LABELS[item]}
            </button>
          ))}
        </div>

        <div className="mt-5 grid gap-4">
          <div>
            <label htmlFor="llm-api-key" className="field-label">
              API Key
            </label>
            <input
              id="llm-api-key"
              type="password"
              autoComplete="off"
              className="text-input"
              value={draft[provider].api_key}
              onChange={(event) => updateDraft('api_key', event.target.value)}
              placeholder={
                selected?.configured
                  ? `已保存 ${selected.masked_key}，留空保持不变`
                  : '未配置，请输入 API Key'
              }
            />
          </div>
          <div className="grid gap-4 md:grid-cols-2">
            <div>
              <label htmlFor="llm-base-url" className="field-label">
                Base URL
              </label>
              <input
                id="llm-base-url"
                className="text-input"
                value={draft[provider].base_url}
                onChange={(event) => updateDraft('base_url', event.target.value)}
              />
            </div>
            <div>
              <label htmlFor="llm-model" className="field-label">
                模型
              </label>
              <input
                id="llm-model"
                className="text-input"
                value={draft[provider].model}
                onChange={(event) => updateDraft('model', event.target.value)}
              />
            </div>
          </div>
        </div>

        {error && (
          <div className="mt-4 flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">
            <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
            <span>{error}</span>
          </div>
        )}

        <div className="mt-5 flex flex-wrap items-center justify-end gap-3">
          <div className="mr-auto flex flex-col gap-2 text-xs text-zinc-500">
            <span>保存后立即生效，无需重启后端。</span>
            {testResult && (
              <span className="inline-flex items-center gap-1 font-medium text-emerald-600">
                <Activity className="size-3.5" aria-hidden="true" />
                {testResult}
              </span>
            )}
            {testError && (
              <span className="inline-flex items-center gap-1 font-medium text-red-600">
                <AlertTriangle className="size-3.5" aria-hidden="true" />
                {testError}
              </span>
            )}
          </div>
          <button
            type="button"
            className="secondary-btn"
            onClick={() => void testConnection()}
            disabled={testing || busy}
          >
            {testing ? (
              <Loader2 className="size-4 animate-spin" aria-hidden="true" />
            ) : (
              <Activity className="size-4" aria-hidden="true" />
            )}
            测试连接
          </button>
          <button type="button" className="secondary-btn" onClick={onClose} disabled={busy}>
            取消
          </button>
          <button
            type="button"
            className="primary-btn"
            onClick={() => void submit()}
            disabled={busy}
          >
            {busy ? (
              <Loader2 className="size-4 animate-spin" aria-hidden="true" />
            ) : (
              <CheckCircle2 className="size-4" aria-hidden="true" />
            )}
            保存配置
          </button>
        </div>
      </section>
    </div>
  )
}
