import {
  AlertTriangle,
  Bot,
  BrainCircuit,
  Flame,
  Loader2,
  Mic,
  MonitorSmartphone,
  RefreshCw,
  Square,
  TrendingUp,
} from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import {
  analyzeExpression,
  getExpressionProgress,
  getExpressionQuestion,
  listInterviewRoles,
} from '../api'
import { useSpeech } from '../hooks/useSpeech'
import type {
  ExpressionProgress,
  ExpressionQuestion,
  ExpressionSession,
  InterviewRoleOption,
} from '../types'

const ROLE_ICONS: Record<string, typeof Bot> = {
  robot_hardware: Bot,
  software_qa: MonitorSmartphone,
  ai_qa: BrainCircuit,
}

const SCORE_LABEL: Record<string, string> = {
  fluency: '流畅度',
  structure: '结构化',
  confidence: '笃定感',
}

function formatClock(seconds: number): string {
  const minutes = Math.floor(seconds / 60)
  const rest = seconds % 60
  return `${String(minutes).padStart(2, '0')}:${String(rest).padStart(2, '0')}`
}

export function ExpressionDrill() {
  const [roles, setRoles] = useState<InterviewRoleOption[]>([])
  const [roleKey, setRoleKey] = useState(
    () => window.localStorage.getItem('opc-expression-role') ?? 'ai_qa',
  )
  const [question, setQuestion] = useState<ExpressionQuestion | null>(null)
  const [transcript, setTranscript] = useState('')
  const [elapsed, setElapsed] = useState(0)
  const [session, setSession] = useState<ExpressionSession | null>(null)
  const [progress, setProgress] = useState<ExpressionProgress | null>(null)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const timerRef = useRef<number | null>(null)
  const startedAtRef = useRef(0)
  const accumulatedRef = useRef('')
  const interimTextRef = useRef('')

  const { isListening, interimText, recognitionSupported, startListening, stopListening, speechError } =
    useSpeech({
      lang: 'zh-CN',
      onFinalText: (text) => {
        accumulatedRef.current += text
        setTranscript(`${accumulatedRef.current}${interimTextRef.current}`)
      },
    })
  interimTextRef.current = interimText

  const refreshProgress = useCallback(async () => {
    try {
      setProgress(await getExpressionProgress())
    } catch {
      // 首次使用时还没有数据，静默即可。
    }
  }, [])

  useEffect(() => {
    listInterviewRoles().then((items) => {
      setRoles(items)
      const initial = items.some((item) => item.key === roleKey)
        ? roleKey
        : items[0]?.key ?? 'ai_qa'
      if (initial !== roleKey) {
        setRoleKey(initial)
      }
      void loadQuestion(initial)
    })
    void refreshProgress()
  }, [])

  const loadQuestion = useCallback(
    async (key: string) => {
      setBusy('question')
      setError('')
      setSession(null)
      try {
        setQuestion(await getExpressionQuestion(key))
      } catch (loadError) {
        setError(loadError instanceof Error ? loadError.message : '取题失败。')
      } finally {
        setBusy('')
      }
    },
    [],
  )

  const chooseRole = (key: string) => {
    setRoleKey(key)
    window.localStorage.setItem('opc-expression-role', key)
    setTranscript('')
    setSession(null)
    void loadQuestion(key)
  }

  const startAnswer = () => {
    setError('')
    setSession(null)
    setTranscript('')
    accumulatedRef.current = ''
    startedAtRef.current = Date.now()
    setElapsed(0)
    if (recognitionSupported) startListening()
    timerRef.current = window.setInterval(() => {
      setElapsed(Math.floor((Date.now() - startedAtRef.current) / 1000))
    }, 500)
  }

  const stopAnswer = () => {
    if (isListening) {
      const recognized = stopListening()
      accumulatedRef.current = recognized
      setTranscript(recognized)
    }
    if (timerRef.current) {
      window.clearInterval(timerRef.current)
      timerRef.current = null
    }
    const seconds = Math.max(1, Math.round((Date.now() - startedAtRef.current) / 1000))
    setElapsed(seconds)
  }

  useEffect(() => {
    return () => {
      if (timerRef.current) window.clearInterval(timerRef.current)
    }
  }, [])

  const submit = async () => {
    if (!question) return
    const content = transcript.trim()
    if (content.length < 10) {
      setError('回答太短了，先说够一句话再提交。')
      return
    }
    setBusy('analyze')
    setError('')
    try {
      const result = await analyzeExpression({
        role_key: roleKey,
        question: question.question,
        question_label: question.label,
        transcript: content,
        duration_sec: elapsed || Math.max(1, Math.round(content.length / 3.2)),
      })
      setSession(result)
      await refreshProgress()
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : '分析失败。')
    } finally {
      setBusy('')
    }
  }

  const goalDone = Math.min(progress?.today_count ?? 0, progress?.daily_goal ?? 3)

  return (
    <div className="space-y-4">
      <section className="tool-card">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="flex items-center gap-2 text-base font-semibold text-zinc-900">
            <Mic className="size-5 text-emerald-600" aria-hidden="true" />
            表达训练
          </h2>
          <p className="text-xs text-zinc-500">
            一次一题，只练怎么说，不考知识点。每天 {progress?.daily_goal ?? 3} 题，紧张和口头禅会随趋势一起改善。
          </p>
        </div>

        {progress && progress.total_sessions > 0 && (
          <dl className="mt-4 grid grid-cols-3 gap-2 text-center">
            <div className="rounded-lg bg-zinc-50 px-2 py-2.5">
              <dt className="flex items-center justify-center gap-1 text-xs text-zinc-500">
                <Flame className="size-3.5 text-orange-500" aria-hidden="true" />
                连续练习
              </dt>
              <dd className="mt-0.5 text-lg font-semibold text-zinc-900">{progress.streak_days} 天</dd>
            </div>
            <div className="rounded-lg bg-zinc-50 px-2 py-2.5">
              <dt className="text-xs text-zinc-500">今日进度</dt>
              <dd className="mt-0.5 text-lg font-semibold text-zinc-900">
                {goalDone}/{progress.daily_goal}
              </dd>
            </div>
            <div className="rounded-lg bg-zinc-50 px-2 py-2.5">
              <dt className="flex items-center justify-center gap-1 text-xs text-zinc-500">
                <TrendingUp className="size-3.5 text-emerald-600" aria-hidden="true" />
                累计
              </dt>
              <dd className="mt-0.5 text-lg font-semibold text-zinc-900">{progress.total_sessions} 题</dd>
            </div>
          </dl>
        )}

        <div className="mt-4 grid grid-cols-3 gap-2">
          {roles.map((role) => {
            const Icon = ROLE_ICONS[role.key] ?? Bot
            const active = role.key === roleKey
            return (
              <button
                key={role.key}
                type="button"
                onClick={() => chooseRole(role.key)}
                className={`flex items-center justify-center gap-2 rounded-lg border px-3 py-2.5 text-sm font-medium transition ${
                  active
                    ? 'border-emerald-500 bg-emerald-50 text-emerald-800'
                    : 'border-zinc-200 bg-white text-zinc-600 hover:border-zinc-300'
                }`}
              >
                <Icon className="size-4" aria-hidden="true" />
                {role.name.replace(/（.*$/, '')}
              </button>
            )
          })}
        </div>
      </section>

      <section className="tool-card">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="text-xs font-medium text-zinc-500">
              {question ? `真题 ${question.label || ''} · ${question.source}` : '选好岗位后取一道真题开始'}
            </p>
            <p className="mt-1.5 text-[15px] font-medium leading-7 text-zinc-900">
              {question?.question ?? '点击右侧按钮，从你的真实面试题库里抽一道表达练习题。'}
            </p>
          </div>
          <button
            type="button"
            className="secondary-btn shrink-0"
            onClick={() => void loadQuestion(roleKey)}
            disabled={Boolean(busy)}
          >
            {busy === 'question' ? (
              <Loader2 className="size-4 animate-spin" aria-hidden="true" />
            ) : (
              <RefreshCw className="size-4" aria-hidden="true" />
            )}
            换一题
          </button>
        </div>

        <div className="mt-4 flex flex-wrap items-center gap-3">
          {!isListening ? (
            <button
              type="button"
              className="primary-btn"
              onClick={startAnswer}
              disabled={!question || Boolean(busy)}
            >
              <Mic className="size-4" aria-hidden="true" />
              开始口述
            </button>
          ) : (
            <button
              type="button"
              className="inline-flex items-center gap-2 rounded-lg border border-red-300 bg-red-50 px-4 py-2 text-sm font-medium text-red-700 transition hover:bg-red-100"
              onClick={stopAnswer}
            >
              <Square className="size-4" aria-hidden="true" />
              结束口述
            </button>
          )}
          <span className={`font-mono text-sm ${isListening ? 'text-red-600' : 'text-zinc-500'}`}>
            {formatClock(elapsed)}
          </span>
          {isListening && <span className="text-xs text-zinc-500">正在听你说，说完点「结束口述」</span>}
          {!recognitionSupported && (
            <span className="text-xs text-amber-700">浏览器不支持实时识别，可直接在下方打字作答。</span>
          )}
        </div>

        {(isListening || interimText) && (
          <p className="mt-2 rounded-lg bg-zinc-50 px-3 py-2 text-sm text-zinc-500">{interimText}…</p>
        )}

        <label htmlFor="expression-transcript" className="field-label mt-4 block">
          回答内容（识别结果可直接修改）
        </label>
        <textarea
          id="expression-transcript"
          className="text-input mt-1 min-h-32 resize-y leading-7"
          value={isListening ? `${transcript}${interimText}` : transcript}
          onChange={(event) => {
            if (!isListening) setTranscript(event.target.value)
          }}
          placeholder="点「开始口述」直接说，或在这里把你的回答打出来。目标 60 到 90 秒：先给结论，再讲两三点，最后一句收束。"
        />

        {(error || speechError) && (
          <p className="mt-3 flex items-start gap-2 text-sm leading-5 text-red-600">
            <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
            {error || speechError}
          </p>
        )}

        <button
          type="button"
          className="primary-btn mt-4 w-full py-3"
          onClick={() => void submit()}
          disabled={Boolean(busy) || isListening || transcript.trim().length < 10}
        >
          {busy === 'analyze' ? (
            <Loader2 className="size-5 animate-spin" aria-hidden="true" />
          ) : (
            <TrendingUp className="size-5" aria-hidden="true" />
          )}
          {busy === 'analyze' ? '正在分析表达' : '提交，看这一轮的表达反馈'}
        </button>
      </section>

      {session && (
        <section className="tool-card">
          <div className="flex items-center justify-between gap-2">
            <h2 className="text-base font-semibold text-zinc-900">本轮反馈</h2>
            <button type="button" className="secondary-btn" onClick={() => void loadQuestion(roleKey)}>
              <RefreshCw className="size-4" aria-hidden="true" />
              练下一题
            </button>
          </div>

          <div className="mt-4 grid grid-cols-3 gap-3">
            {(['fluency', 'structure', 'confidence'] as const).map((key) => (
              <div key={key}>
                <div className="flex items-center justify-between text-xs text-zinc-500">
                  <span>{SCORE_LABEL[key]}</span>
                  <span className="font-semibold text-zinc-800">{session.metrics.scores[key]}</span>
                </div>
                <div className="mt-1 h-2 w-full overflow-hidden rounded-lg bg-zinc-200">
                  <div
                    className={`h-full rounded-lg ${
                      session.metrics.scores[key] >= 80
                        ? 'bg-emerald-600'
                        : session.metrics.scores[key] >= 60
                          ? 'bg-amber-500'
                          : 'bg-red-500'
                    }`}
                    style={{ width: `${session.metrics.scores[key]}%` }}
                  />
                </div>
              </div>
            ))}
          </div>

          <div className="mt-4 flex flex-wrap gap-2 text-xs">
            <span className="rounded-full bg-zinc-100 px-2.5 py-1 text-zinc-600">
              语速 {session.metrics.rate_cpm} 字/分
            </span>
            <span className="rounded-full bg-zinc-100 px-2.5 py-1 text-zinc-600">
              语气词 {session.metrics.filler_total} 个
            </span>
            <span className="rounded-full bg-zinc-100 px-2.5 py-1 text-zinc-600">
              口头禅 {session.metrics.crutch_total} 次
            </span>
            <span className="rounded-full bg-zinc-100 px-2.5 py-1 text-zinc-600">
              卡顿重复 {session.metrics.restart_count} 处
            </span>
            {session.metrics.structure_markers.length > 0 && (
              <span className="rounded-full bg-emerald-50 px-2.5 py-1 text-emerald-700">
                结构词 {session.metrics.structure_markers.join('、')}
              </span>
            )}
          </div>

          <p className="mt-4 rounded-lg bg-emerald-50 px-3 py-2.5 text-sm leading-6 text-emerald-900">
            {session.coach.summary}
          </p>

          {session.coach.strengths.length > 0 && (
            <div className="mt-3">
              <p className="text-xs font-semibold text-zinc-500">这轮做得好的</p>
              <ul className="mt-1 space-y-1 text-sm leading-6 text-zinc-700">
                {session.coach.strengths.map((item, index) => (
                  <li key={index}>· {item}</li>
                ))}
              </ul>
            </div>
          )}

          <div className="mt-3">
            <p className="text-xs font-semibold text-zinc-500">怎么改</p>
            <ul className="mt-1 space-y-1 text-sm leading-6 text-zinc-700">
              {session.coach.fixes.map((item, index) => (
                <li key={index}>{index + 1}. {item}</li>
              ))}
            </ul>
          </div>

          {session.coach.example && (
            <div className="mt-3">
              <p className="text-xs font-semibold text-zinc-500">一句话改写示范</p>
              <p className="mt-1 rounded-lg bg-zinc-50 px-3 py-2 text-sm leading-6 text-zinc-800">
                {session.coach.example}
              </p>
            </div>
          )}

          <div className="mt-3 grid gap-2 sm:grid-cols-2">
            {session.coach.next_focus && (
              <p className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm leading-6 text-amber-900">
                下轮重点：{session.coach.next_focus}
              </p>
            )}
            {session.coach.mindset_tip && (
              <p className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm leading-6 text-emerald-900">
                临场心态：{session.coach.mindset_tip}
              </p>
            )}
          </div>
        </section>
      )}

      {progress && progress.trend.length >= 2 && (
        <section className="tool-card">
          <h2 className="text-base font-semibold text-zinc-900">最近 {progress.trend.length} 次趋势</h2>
          <div className="mt-3 space-y-1.5">
            {progress.trend.slice(-10).map((item, index) => (
              <div key={index} className="flex items-center gap-2 text-xs text-zinc-500">
                <span className="w-16 shrink-0 truncate">
                  {new Date(item.created_at * 1000).toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' })}
                </span>
                <div className="h-2 flex-1 overflow-hidden rounded-lg bg-zinc-100">
                  <div
                    className="h-full rounded-lg bg-emerald-500"
                    style={{ width: `${item.fluency}%` }}
                  />
                </div>
                <span className="w-8 shrink-0 text-right font-medium text-zinc-700">{item.fluency}</span>
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  )
}
