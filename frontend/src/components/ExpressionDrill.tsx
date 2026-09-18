import {
  AlertTriangle,
  BookOpen,
  EyeOff,
  Flame,
  ListChecks,
  Loader2,
  Mic,
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
  transcribeExpressionClip,
} from '../api'
import type {
  ExpressionProgress,
  ExpressionQuestion,
  ExpressionPracticeMode,
  ExpressionSession,
  InterviewRoleOption,
} from '../types'

const SCORE_LABEL: Record<string, string> = {
  fluency: '流畅度',
  structure: '结构化',
  confidence: '笃定感',
}

const HINT_MODES: Array<{
  key: ExpressionPracticeMode
  label: string
  description: string
  icon: typeof BookOpen
}> = [
  { key: 'read', label: '照读答案', description: '完整展示题库参考答案，先照着读，找停顿、节奏和表达感觉。', icon: BookOpen },
  { key: 'keywords', label: '关键词', description: '只保留答案线索，自己把关键词串成一段完整回答。', icon: ListChecks },
  { key: 'blind', label: '无提示', description: '不展示答案，按真实面试状态直接表达，用于判断是否真正掌握。', icon: EyeOff },
]

const MODE_LABEL: Record<ExpressionPracticeMode, string> = {
  read: '照读答案',
  keywords: '关键词串联',
  blind: '无提示实战',
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
  const [hintMode, setHintMode] = useState<ExpressionPracticeMode>(() => {
    const saved = window.localStorage.getItem('opc-expression-hint-mode')
    return saved === 'read' || saved === 'keywords' || saved === 'blind' ? saved : 'read'
  })
  const [question, setQuestion] = useState<ExpressionQuestion | null>(null)
  const [transcript, setTranscript] = useState('')
  const [elapsed, setElapsed] = useState(0)
  const [session, setSession] = useState<ExpressionSession | null>(null)
  const [progress, setProgress] = useState<ExpressionProgress | null>(null)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const timerRef = useRef<number | null>(null)
  const startedAtRef = useRef(0)
  const streamRef = useRef<MediaStream | null>(null)
  const recorderRef = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<Blob[]>([])
  const shouldTranscribeRef = useRef(false)
  const answerRoundRef = useRef(0)
  const [mediaSupported] = useState(
    () =>
      typeof navigator !== 'undefined' &&
      Boolean(navigator.mediaDevices?.getUserMedia) &&
      typeof MediaRecorder !== 'undefined',
  )

  const clearTimer = () => {
    if (timerRef.current) {
      window.clearInterval(timerRef.current)
      timerRef.current = null
    }
  }

  const cancelActiveRecording = useCallback(() => {
    answerRoundRef.current += 1
    shouldTranscribeRef.current = false
    clearTimer()
    streamRef.current?.getTracks().forEach((track) => track.stop())
    if (recorderRef.current?.state === 'recording') {
      recorderRef.current.stop()
    }
    streamRef.current = null
    recorderRef.current = null
    chunksRef.current = []
    setElapsed(0)
  }, [])

  const refreshProgress = useCallback(async () => {
    try {
      setProgress(await getExpressionProgress())
    } catch {
      // 首次使用时还没有数据，静默即可。
    }
  }, [])

  const loadQuestion = useCallback(
    async (key: string) => {
      answerRoundRef.current += 1
      cancelActiveRecording()
      setBusy('question')
      setError('')
      setSession(null)
      setTranscript('')
      try {
        setQuestion(await getExpressionQuestion(key))
      } catch (loadError) {
        setError(loadError instanceof Error ? loadError.message : '取题失败。')
      } finally {
        setBusy('')
      }
    },
    [cancelActiveRecording],
  )

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
    // 只在首次挂载时加载岗位和题目；切换岗位由 chooseRole 显式触发，避免重复取题。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const chooseRole = (key: string) => {
    if (key === roleKey && (busy === 'record' || busy === 'transcribing')) return
    setRoleKey(key)
    window.localStorage.setItem('opc-expression-role', key)
    setSession(null)
    void loadQuestion(key)
  }

  const chooseHintMode = (mode: ExpressionPracticeMode) => {
    if (mode === hintMode || busy === 'record' || busy === 'transcribing' || busy === 'analyze') return
    answerRoundRef.current += 1
    cancelActiveRecording()
    setHintMode(mode)
    window.localStorage.setItem('opc-expression-hint-mode', mode)
    setSession(null)
    setError('')
    setTranscript('')
    setElapsed(0)
  }

  const resetAttempt = () => {
    answerRoundRef.current += 1
    cancelActiveRecording()
    setSession(null)
    setError('')
    setTranscript('')
    setElapsed(0)
  }

  const nextQuestionFromStart = () => {
    setHintMode('read')
    window.localStorage.setItem('opc-expression-hint-mode', 'read')
    void loadQuestion(roleKey)
  }

  const followProgression = () => {
    if (!session?.progression) return
    const next = session.progression.next_mode
    if (next === 'next_question') {
      nextQuestionFromStart()
      return
    }
    chooseHintMode(next)
  }

  const handleProgressionClick = () => {
    if (!session?.progression) return
    if (session.progression.next_mode === hintMode) {
      resetAttempt()
      return
    }
    followProgression()
  }

  const startAnswer = async () => {
    if (!mediaSupported || busy === 'record' || busy === 'transcribing') return
    const round = answerRoundRef.current + 1
    answerRoundRef.current = round
    setError('')
    setSession(null)
    setTranscript('')
    setElapsed(0)
    setBusy('record')
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      if (round !== answerRoundRef.current || !shouldTranscribeRef.current) {
        stream.getTracks().forEach((track) => track.stop())
        return
      }
      const mimeType = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4'].find(
        (type) => MediaRecorder.isTypeSupported(type),
      )
      const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined)
      chunksRef.current = []
      streamRef.current = stream
      recorderRef.current = recorder
      shouldTranscribeRef.current = true

      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) chunksRef.current.push(event.data)
      }
      recorder.onstop = async () => {
        stream.getTracks().forEach((track) => track.stop())
        if (!shouldTranscribeRef.current || round !== answerRoundRef.current) return
        setBusy('transcribing')
        try {
          const blob = new Blob(chunksRef.current, {
            type: recorder.mimeType || mimeType || 'audio/webm',
          })
          const result = await transcribeExpressionClip(blob)
          if (round !== answerRoundRef.current) return
          setTranscript(result.text)
          setElapsed(Math.max(1, Math.round(result.duration)))
        } catch (transcribeError) {
          if (round !== answerRoundRef.current) return
          setError(
            transcribeError instanceof Error
              ? transcribeError.message
              : '本地语音识别失败，请重试或直接粘贴文字。',
          )
        } finally {
          chunksRef.current = []
          streamRef.current = null
          recorderRef.current = null
          if (round === answerRoundRef.current) setBusy('')
        }
      }

      recorder.start(500)
    startedAtRef.current = Date.now()
    setElapsed(0)
      timerRef.current = window.setInterval(() => {
        setElapsed(Math.floor((Date.now() - startedAtRef.current) / 1000))
      }, 500)
    } catch (recordError) {
      if (round !== answerRoundRef.current) return
      streamRef.current?.getTracks().forEach((track) => track.stop())
      streamRef.current = null
      recorderRef.current = null
      setBusy('')
      if (recordError instanceof DOMException && recordError.name === 'NotAllowedError') {
        setError('麦克风权限被拒绝，请在浏览器地址栏允许麦克风后重试。')
      } else if (recordError instanceof DOMException && recordError.name === 'NotFoundError') {
        setError('没有检测到麦克风设备，可以直接在下方打字或粘贴转写。')
      } else if (recordError instanceof DOMException && recordError.name === 'NotReadableError') {
        setError('麦克风暂时被其他应用占用，请关闭占用后重试。')
      } else {
        setError('无法启动麦克风录音，请检查设备权限，或直接打字作答。')
      }
    }
  }

  const stopAnswer = () => {
    clearTimer()
    const seconds = Math.max(1, Math.round((Date.now() - startedAtRef.current) / 1000))
    setElapsed(seconds)
    recorderRef.current?.stop()
  }

  useEffect(() => {
    return () => {
      shouldTranscribeRef.current = false
      clearTimer()
      streamRef.current?.getTracks().forEach((track) => track.stop())
      if (recorderRef.current?.state === 'recording') recorderRef.current.stop()
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
        practice_mode: hintMode,
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

        {progress && progress.total_sessions > 0 && (
          <div className="mt-3 flex flex-wrap gap-2 text-xs text-zinc-500">
            <span className="rounded-full bg-zinc-50 px-2.5 py-1">
              照读 {progress.mode_counts?.read ?? 0} 次
            </span>
            <span className="rounded-full bg-zinc-50 px-2.5 py-1">
              关键词 {progress.mode_counts?.keywords ?? 0} 次
            </span>
            <span className="rounded-full bg-emerald-50 px-2.5 py-1 text-emerald-700">
              无提示 {progress.blind_total ?? 0} 次 · 均分 {progress.blind_average || '--'}
            </span>
          </div>
        )}

        <div className="mt-4">
          <label className="field-label" htmlFor="expression-role">
            练习方向
          </label>
          <select
            id="expression-role"
            className="text-input disabled:cursor-not-allowed disabled:bg-zinc-50 disabled:text-zinc-400"
            value={roleKey}
            onChange={(event) => chooseRole(event.target.value)}
            disabled={roles.length === 0 || Boolean(busy)}
          >
            {roles.length === 0 ? (
              <option value="">岗位加载中...</option>
            ) : (
              roles.map((role) => (
                <option key={role.key} value={role.key}>
                  {role.name}
                </option>
              ))
            )}
          </select>
          <p className="mt-2 text-xs leading-5 text-zinc-500">
            {roles.find((role) => role.key === roleKey)?.summary ?? '正在加载练习方向。'}
          </p>
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

        <div className="mt-4 grid grid-cols-3 gap-2">
          {HINT_MODES.map((mode) => {
            const HintIcon = mode.icon
            const active = hintMode === mode.key
            return (
              <button
                key={mode.key}
                type="button"
                onClick={() => chooseHintMode(mode.key)}
                disabled={busy === 'record' || busy === 'transcribing' || busy === 'analyze'}
                className={`flex items-center justify-center gap-1.5 rounded-lg border px-2 py-2 text-xs font-medium transition ${
                  active
                    ? 'border-emerald-500 bg-emerald-50 text-emerald-800'
                    : 'border-zinc-200 bg-white text-zinc-600 hover:border-zinc-300'
                }`}
              >
                <HintIcon className="size-3.5" aria-hidden="true" />
                {mode.label}
              </button>
            )
          })}
        </div>
        <p className="mt-2 text-xs leading-5 text-zinc-500">
          {HINT_MODES.find((mode) => mode.key === hintMode)?.description}
        </p>

        {question && hintMode === 'read' && (
          <div className="mt-3 rounded-lg bg-emerald-50 p-3">
            <p className="text-xs font-semibold text-emerald-900">
              题库参考答案 · 照着读，先找语感
            </p>
            {question.reference_answer ? (
              <p className="mt-2 max-h-72 overflow-y-auto whitespace-pre-line pr-1 text-sm leading-7 text-emerald-950">
                {question.reference_answer}
              </p>
            ) : (
              <p className="mt-2 text-sm leading-6 text-amber-800">
                这道题没有抽取到参考答案，建议先切换到关键词或无提示模式。
              </p>
            )}
          </div>
        )}

        {question && hintMode === 'keywords' && (
          <div className="mt-3 rounded-lg bg-zinc-50 p-3">
            <p className="text-xs font-semibold text-zinc-700">只看关键词，自己串成完整回答</p>
            <div className="mt-2 flex flex-wrap gap-2">
              {question.keywords.length > 0 ? (
                question.keywords.map((keyword) => (
                  <span
                    key={keyword}
                    className="rounded-full border border-zinc-200 bg-white px-2.5 py-1 text-xs text-zinc-700"
                  >
                    {keyword}
                  </span>
                ))
              ) : (
                <span className="text-xs text-zinc-500">暂无关键词，请直接按题干组织答案。</span>
              )}
            </div>
          </div>
        )}

        {question && hintMode === 'blind' && (
          <div className="mt-3 rounded-lg border border-zinc-200 bg-white px-3 py-2.5 text-xs leading-5 text-zinc-600">
            无提示模式：不展示答案和关键词。先停半秒给结论，再展开两三个要点，最后一句收束。
          </div>
        )}

        <div className="mt-4 flex flex-wrap items-center gap-3">
          {busy !== 'record' ? (
            <button
              type="button"
              className="primary-btn"
              onClick={startAnswer}
              disabled={!question || !mediaSupported || Boolean(busy)}
            >
              {busy === 'transcribing' ? (
                <Loader2 className="size-4 animate-spin" aria-hidden="true" />
              ) : (
                <Mic className="size-4" aria-hidden="true" />
              )}
              {busy === 'transcribing' ? '本地识别中' : '开始口述'}
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
          <span
            className={`font-mono text-sm ${
              busy === 'record'
                ? 'text-red-600'
                : busy === 'transcribing'
                  ? 'text-amber-600'
                  : 'text-zinc-500'
            }`}
          >
            {formatClock(elapsed)}
          </span>
          {busy === 'record' && <span className="text-xs text-zinc-500">正在录音，说完点「结束口述」</span>}
          {busy === 'transcribing' && (
            <span className="text-xs text-amber-700">本地 Whisper 转写中，会尽量保留语气词和卡顿。</span>
          )}
          {!mediaSupported && (
            <span className="text-xs text-amber-700">当前浏览器不支持录音，可直接打字或粘贴转写作答。</span>
          )}
        </div>

        <label htmlFor="expression-transcript" className="field-label mt-4 block">
          回答内容（识别结果可直接修改）
        </label>
        <textarea
          id="expression-transcript"
          className="text-input mt-1 min-h-32 resize-y leading-7"
          value={transcript}
          onChange={(event) => setTranscript(event.target.value)}
          disabled={busy === 'record' || busy === 'transcribing'}
          placeholder="点「开始口述」直接说，或在这里把你的回答打出来。目标 60 到 90 秒：先给结论，再讲两三点，最后一句收束。"
        />
        <p className="mt-2 text-xs leading-5 text-zinc-500">
          本地 Whisper 按逐字稿转写；提交前可快速校对，评分以文本框中的最终内容和真实音频时长为准。
        </p>

        {error && (
          <p className="mt-3 flex items-start gap-2 text-sm leading-5 text-red-600">
            <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
            {error}
          </p>
        )}

        <button
          type="button"
          className="primary-btn mt-4 w-full py-3"
          onClick={() => void submit()}
          disabled={Boolean(busy) || transcript.trim().length < 10}
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
            <div className="flex items-center gap-2">
              <h2 className="text-base font-semibold text-zinc-900">本轮反馈</h2>
              <span className="rounded-full bg-zinc-100 px-2 py-0.5 text-xs text-zinc-600">
                {MODE_LABEL[session.practice_mode]}
              </span>
            </div>
            <button type="button" className="secondary-btn" onClick={() => void loadQuestion(roleKey)}>
              <RefreshCw className="size-4" aria-hidden="true" />
              练下一题
            </button>
          </div>

          <div
            className={`mt-3 rounded-lg border px-3 py-2.5 ${
              session.progression.passed
                ? 'border-emerald-200 bg-emerald-50'
                : 'border-amber-200 bg-amber-50'
            }`}
          >
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p
                className={`text-sm font-medium ${
                  session.progression.passed ? 'text-emerald-900' : 'text-amber-900'
                }`}
              >
                {session.progression.passed ? '本档通过' : '建议继续巩固'}
              </p>
              <button
                type="button"
                className={session.progression.passed ? 'primary-btn px-3 py-1.5 text-xs' : 'secondary-btn px-3 py-1.5 text-xs'}
                onClick={handleProgressionClick}
              >
                {session.progression.next_mode === 'next_question'
                  ? '通过，换一道新题'
                  : session.progression.next_mode === hintMode
                    ? '同一题再练一次'
                    : `下一阶：${MODE_LABEL[session.progression.next_mode]}`}
              </button>
            </div>
            <p
              className={`mt-1.5 text-sm leading-6 ${
                session.progression.passed ? 'text-emerald-800' : 'text-amber-800'
              }`}
            >
              {session.progression.message}
            </p>
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
          <h2 className="text-base font-semibold text-zinc-900">
            最近 {progress.trend.length} 次趋势（照读/关键词/无提示分开看）
          </h2>
          <div className="mt-3 space-y-1.5">
            {progress.trend.slice(-10).map((item, index) => (
              <div key={index} className="flex items-center gap-2 text-xs text-zinc-500">
                <span className="w-20 shrink-0 truncate text-[10px] leading-4">
                  {new Date(item.created_at * 1000).toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' })}
                  <span className="ml-1 text-zinc-400">{MODE_LABEL[item.practice_mode ?? 'blind'].slice(0, 2)}</span>
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
