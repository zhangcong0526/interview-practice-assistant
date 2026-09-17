import {
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  Lightbulb,
  Loader2,
  RefreshCw,
} from 'lucide-react'
import { useEffect, useState } from 'react'
import { generateResumeAdvice, loadResumeAdvice } from '../api'
import type { ResumeAdvice, ResumeAdviceSuggestion, ResumeRecord } from '../types'

interface ResumeAdvisorProps {
  activeResume: ResumeRecord | null
}

const PRIORITY_META: Record<string, { label: string; className: string }> = {
  high: { label: '优先改', className: 'bg-red-100 text-red-700' },
  medium: { label: '建议改', className: 'bg-amber-100 text-amber-700' },
  low: { label: '可选', className: 'bg-zinc-100 text-zinc-600' },
}

export function ResumeAdvisor({ activeResume }: ResumeAdvisorProps) {
  const [advice, setAdvice] = useState<ResumeAdvice | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [expanded, setExpanded] = useState<number>(-1)

  useEffect(() => {
    setAdvice(null)
    setError('')
    if (!activeResume) return
    loadResumeAdvice(activeResume.resume_id).then((cached) => {
      if (cached && cached.char_count === activeResume.char_count) {
        setAdvice(cached)
      }
    })
  }, [activeResume])

  const handleGenerate = async (refresh: boolean) => {
    if (busy) return
    setBusy(true)
    setError('')
    try {
      setAdvice(await generateResumeAdvice(refresh))
    } catch (generateError) {
      setError(generateError instanceof Error ? generateError.message : '建议生成失败。')
    } finally {
      setBusy(false)
    }
  }

  if (!activeResume) return null

  return (
    <section className="tool-card">
      <div className="flex items-center justify-between gap-3">
        <h2 className="flex items-center gap-2 text-base font-semibold text-zinc-900">
          <Lightbulb className="size-5 text-emerald-600" aria-hidden="true" />
          简历优化建议
        </h2>
        <button
          type="button"
          className="secondary-btn"
          onClick={() => void handleGenerate(Boolean(advice))}
          disabled={busy}
        >
          {busy ? (
            <Loader2 className="size-4 animate-spin" aria-hidden="true" />
          ) : advice ? (
            <RefreshCw className="size-4" aria-hidden="true" />
          ) : (
            <Lightbulb className="size-4" aria-hidden="true" />
          )}
          {busy ? '正在分析' : advice ? '重新生成' : '生成建议'}
        </button>
      </div>

      <p className="mt-2 text-xs leading-5 text-zinc-500">
        结合真实面试题库的高频考点、你的刷题掌握度和简历档案标注的缺口，指出简历最该改的地方。
        改写示范中【待补充】的部分需要你填入真实数字，系统不会替你编造。
      </p>

      {error && (
        <p className="mt-3 flex items-start gap-2 text-sm leading-5 text-red-600">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          {error}
        </p>
      )}

      {advice && (
        <>
          <p className="mt-3 text-xs text-zinc-500">
            依据 {advice.question_count} 道真实面试题
            {advice.weak_topic_count > 0 ? ` · ${advice.weak_topic_count} 个低分知识点` : ''}
            {' · '}
            {new Date(advice.created_at * 1000).toLocaleString('zh-CN', { hour12: false })}
          </p>
          <ul className="mt-3 space-y-2">
            {advice.suggestions.map((item: ResumeAdviceSuggestion, index: number) => {
              const open = expanded === index
              const meta = PRIORITY_META[item.priority] ?? PRIORITY_META.medium
              return (
                <li key={index} className="rounded-lg border border-zinc-200 bg-zinc-50">
                  <button
                    type="button"
                    className="flex w-full items-start gap-2 px-3 py-2.5 text-left"
                    onClick={() => setExpanded(open ? -1 : index)}
                    aria-expanded={open}
                  >
                    {open ? (
                      <ChevronDown className="mt-0.5 size-4 shrink-0 text-zinc-400" aria-hidden="true" />
                    ) : (
                      <ChevronRight className="mt-0.5 size-4 shrink-0 text-zinc-400" aria-hidden="true" />
                    )}
                    <span className="min-w-0 flex-1">
                      <span className="flex flex-wrap items-center gap-2">
                        <span className={`rounded px-1.5 py-0.5 text-xs font-medium ${meta.className}`}>
                          {meta.label}
                        </span>
                        <span className="text-sm font-medium text-zinc-800">{item.target}</span>
                      </span>
                      {!open && (
                        <span className="mt-1 block truncate text-xs text-zinc-500">{item.issue}</span>
                      )}
                    </span>
                  </button>
                  {open && (
                    <div className="space-y-2.5 border-t border-zinc-200 px-3 py-3 text-sm leading-6">
                      <div>
                        <p className="text-xs font-semibold text-zinc-500">问题</p>
                        <p className="mt-0.5 text-zinc-800">{item.issue}</p>
                      </div>
                      <div>
                        <p className="text-xs font-semibold text-zinc-500">怎么改</p>
                        <p className="mt-0.5 text-zinc-800">{item.suggestion}</p>
                      </div>
                      {item.example && (
                        <div>
                          <p className="text-xs font-semibold text-zinc-500">改写示范</p>
                          <p className="mt-0.5 rounded-lg bg-white px-3 py-2 text-zinc-700">
                            {item.example}
                          </p>
                        </div>
                      )}
                      {item.evidence.length > 0 && (
                        <div>
                          <p className="text-xs font-semibold text-zinc-500">依据</p>
                          <ul className="mt-0.5 space-y-0.5 text-xs text-zinc-600">
                            {item.evidence.map((line, evidenceIndex) => (
                              <li key={evidenceIndex}>· {line}</li>
                            ))}
                          </ul>
                        </div>
                      )}
                    </div>
                  )}
                </li>
              )
            })}
          </ul>
        </>
      )}
    </section>
  )
}
