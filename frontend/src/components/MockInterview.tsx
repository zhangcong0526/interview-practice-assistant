import {
  AlertTriangle,
  Bot,
  BrainCircuit,
  CheckCheck,
  IdCard,
  Loader2,
  Mic,
  MicOff,
  MonitorSmartphone,
  Send,
  Square,
  UserRound,
  Volume2,
  VolumeX,
} from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import {
  listInterviewRoles,
  listTTSVoices,
  requestInterviewTurn,
  searchKnowledge,
  synthesiseSpeech,
} from '../api'
import type { TTSVoice } from '../api'
import { useSpeech } from '../hooks/useSpeech'
import type { InterviewMessage, InterviewRoleOption } from '../types'

interface MockInterviewProps {
  jd: string
  resume: string
  resumeId?: string
  resumeName?: string
  hasKnowledge: boolean
  onFinished: (transcript: string) => void
}

const QUESTION_OPTIONS = [3, 5, 8]

const ROLE_ICONS: Record<string, typeof Bot> = {
  robot_hardware: Bot,
  software_qa: MonitorSmartphone,
  ai_qa: BrainCircuit,
}

const ROLE_STORAGE_KEY = 'opc-interview-role'

function buildTranscript(messages: InterviewMessage[]): string {
  return messages
    .map(
      (message) =>
        `${message.role === 'interviewer' ? '面试官' : '候选人'}：${message.content}`,
    )
    .join('\n')
}

export function MockInterview({
  jd,
  resume,
  resumeId,
  resumeName,
  hasKnowledge,
  onFinished,
}: MockInterviewProps) {
  const [started, setStarted] = useState(false)
  const [maxQuestions, setMaxQuestions] = useState(5)
  const [roleOptions, setRoleOptions] = useState<InterviewRoleOption[]>([])
  const [roleKey, setRoleKey] = useState(
    () => window.localStorage.getItem(ROLE_STORAGE_KEY) ?? 'robot_hardware',
  )
  const [voiceMode, setVoiceMode] = useState(true)
  const [voices, setVoices] = useState<TTSVoice[]>([])
  const [voiceId, setVoiceId] = useState('')
  const [speechRate, setSpeechRate] = useState(0)
  const [previewing, setPreviewing] = useState(false)
  const [messages, setMessages] = useState<InterviewMessage[]>([])
  const [askedCount, setAskedCount] = useState(0)
  const [isThinking, setIsThinking] = useState(false)
  const [isDone, setIsDone] = useState(false)
  const [typedAnswer, setTypedAnswer] = useState('')
  const [error, setError] = useState('')
  const knowledgeRef = useRef('')
  const scrollRef = useRef<HTMLDivElement | null>(null)
  // 试听音频不走 useSpeech，需要单独持有引用才能在切换岗位时停掉。
  const previewAudioRef = useRef<HTMLAudioElement | null>(null)
  const previewUrlRef = useRef('')
  const previewTokenRef = useRef(0)
  // 一轮对话的世代号。切换岗位或重新开始面试时自增，
  // 让仍在途中的上一岗位请求返回后自行作废。
  const turnTokenRef = useRef(0)

  const {
    isListening,
    isSpeaking,
    interimText,
    speechError,
    recognitionSupported,
    usingFallbackVoice,
    startListening,
    stopListening,
    speak,
    stopSpeaking,
    warmUpOutput,
  } = useSpeech({ lang: 'zh-CN', voice: voiceId, rate: speechRate })

  useEffect(() => {
    let cancelled = false
    listTTSVoices()
      .then((data) => {
        if (cancelled) return
        setVoices(data.voices)
        setVoiceId((current) => current || data.default)
      })
      .catch(() => {
        // 取不到音色列表时沿用后端默认值，不打断面试流程。
      })
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    listInterviewRoles()
      .then((data) => {
        if (cancelled) return
        setRoleOptions(data)
        setRoleKey((current) =>
          data.some((item) => item.key === current) ? current : data[0]?.key ?? '',
        )
      })
      .catch(() => {
        // 岗位列表取不到时退化为通用面试，不阻断流程。
      })
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    if (roleKey) window.localStorage.setItem(ROLE_STORAGE_KEY, roleKey)
  }, [roleKey])

  const stopPreview = useCallback(() => {
    previewTokenRef.current += 1
    const audio = previewAudioRef.current
    if (audio) {
      audio.onended = null
      audio.onerror = null
      audio.pause()
    }
    previewAudioRef.current = null
    if (previewUrlRef.current) {
      URL.revokeObjectURL(previewUrlRef.current)
      previewUrlRef.current = ''
    }
    setPreviewing(false)
  }, [])

  useEffect(() => stopPreview, [stopPreview])

  const handleRoleChange = useCallback(
    (nextKey: string) => {
      if (nextKey === roleKey) return
      // 切岗位要立刻掐掉上一岗位还在播或还在合成的语音，避免两个岗位的声音叠在一起。
      turnTokenRef.current += 1
      stopPreview()
      stopSpeaking()
      stopListening()
      setIsThinking(false)
      setRoleKey(nextKey)
      // 借这次点击（浏览器认可的用户手势）把输出设备唤醒并保持，
      // 这样正式播报时不用再等声卡冷启动。
      void warmUpOutput()
    },
    [roleKey, stopListening, stopPreview, stopSpeaking, warmUpOutput],
  )

  const activeRole = roleOptions.find((item) => item.key === roleKey)
  // JD 留空时用岗位默认 JD 兜底，用户自己填了就以他填的为准。
  const effectiveJd = jd.trim() || activeRole?.default_jd || ''

  useEffect(() => {
    scrollRef.current?.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior: 'smooth',
    })
  }, [messages, interimText, isThinking])

  const runTurn = useCallback(
    async (history: InterviewMessage[], asked: number) => {
      const token = turnTokenRef.current
      setIsThinking(true)
      setError('')
      try {
        const turn = await requestInterviewTurn({
          jd: effectiveJd,
          resume,
          knowledge: knowledgeRef.current,
          history,
          asked_count: asked,
          max_questions: maxQuestions,
          resume_id: resumeId,
          role_key: roleKey,
        })
        // 请求期间若已切换岗位或重开面试，这一轮的结果不能再落到界面上。
        if (token !== turnTokenRef.current) return
        const next: InterviewMessage = {
          role: 'interviewer',
          content: turn.speech,
        }
        setMessages([...history, next])
        if (turn.is_new_question) setAskedCount((value) => value + 1)
        setIsThinking(false)

        if (voiceMode) {
          // 播报期间必须停止识别，否则会把面试官的声音识别成候选人的回答。
          await speak(turn.speech)
        }
        if (token !== turnTokenRef.current) return
        if (turn.is_final) {
          setIsDone(true)
          return
        }
        if (voiceMode && recognitionSupported) startListening()
      } catch (turnError) {
        if (token !== turnTokenRef.current) return
        setIsThinking(false)
        setError(
          turnError instanceof Error ? turnError.message : '面试官响应失败，请重试。',
        )
      }
    },
    [
      effectiveJd,
      resume,
      resumeId,
      maxQuestions,
      roleKey,
      voiceMode,
      recognitionSupported,
      speak,
      startListening,
    ],
  )

  const activeVoice = voices.find((item) => item.id === voiceId)

  const handlePreviewVoice = async () => {
    if (previewing) return
    // 试听和面试官播报共用扬声器，先把正在进行的播报停掉。
    stopSpeaking()
    stopPreview()
    const token = previewTokenRef.current
    setPreviewing(true)
    setError('')
    try {
      const blob = await synthesiseSpeech({
        text: '你好，我是今天的面试官。请先花两三分钟做个自我介绍，重点说说你最近负责的测试项目。',
        voice: voiceId,
        rate: speechRate,
      })
      if (token !== previewTokenRef.current) return
      const url = URL.createObjectURL(blob)
      previewUrlRef.current = url
      const audio = new Audio(url)
      previewAudioRef.current = audio
      await new Promise<void>((resolve) => {
        audio.onended = () => resolve()
        audio.onerror = () => resolve()
        audio.play().catch(() => resolve())
      })
      if (token !== previewTokenRef.current) return
      previewAudioRef.current = null
      previewUrlRef.current = ''
      URL.revokeObjectURL(url)
    } catch (previewError) {
      if (token !== previewTokenRef.current) return
      setError(
        previewError instanceof Error
          ? previewError.message
          : '试听失败，请检查网络后重试。',
      )
    } finally {
      if (token === previewTokenRef.current) setPreviewing(false)
    }
  }

  const handleStart = async () => {
    // 开始新一场面试前，确保没有上一轮残留的语音还在播或还在合成。
    turnTokenRef.current += 1
    stopPreview()
    stopSpeaking()
    void warmUpOutput()
    setStarted(true)
    setMessages([])
    setAskedCount(0)
    setIsDone(false)
    setError('')
    if (hasKnowledge) {
      try {
        // 检索词以岗位关键词为主，简历为辅，避免跨岗位串题。
        const query =
          `${activeRole?.search_query ?? ''}\n${resume.slice(0, 400)}`.trim() ||
          `${effectiveJd}\n${resume}`.trim() ||
          '测试岗位'
        const chunks = await searchKnowledge(query, 4)
        knowledgeRef.current = chunks
          .map((chunk) => `[${chunk.title}] ${chunk.text}`)
          .join('\n\n')
      } catch {
        knowledgeRef.current = ''
      }
    }
    await runTurn([], 0)
  }

  const submitAnswer = async (answer: string) => {
    const text = answer.trim()
    if (!text || isThinking) return
    const history: InterviewMessage[] = [
      ...messages,
      { role: 'candidate', content: text },
    ]
    setMessages(history)
    setTypedAnswer('')
    await runTurn(history, askedCount)
  }

  const handleVoiceSubmit = async () => {
    const text = stopListening()
    if (!text) {
      setError('没有听到内容，请再说一次或改用打字回答。')
      return
    }
    await submitAnswer(text)
  }

  const handleFinish = () => {
    stopListening()
    stopSpeaking()
    onFinished(buildTranscript(messages))
  }

  const activeError = error || speechError

  if (!started) {
    return (
      <section className="tool-card">
        <h2 className="flex items-center gap-2 text-base font-semibold text-zinc-900">
          <UserRound className="size-5 text-emerald-600" aria-hidden="true" />
          语音模拟面试
        </h2>
        <p className="mt-2 text-sm leading-6 text-zinc-600">
          AI 面试官会围绕你的简历档案逐轮提问，点名具体项目和数据追问，结束后自动生成评分报告。
        </p>

        {roleOptions.length > 0 && (
          <div className="mt-4">
            <span className="field-label">意向岗位</span>
            <div className="grid gap-2 sm:grid-cols-3">
              {roleOptions.map((option) => {
                const Icon = ROLE_ICONS[option.key] ?? UserRound
                const selected = option.key === roleKey
                return (
                  <button
                    key={option.key}
                    type="button"
                    onClick={() => handleRoleChange(option.key)}
                    aria-pressed={selected}
                    className={`min-w-0 rounded-lg border p-3 text-left ${
                      selected
                        ? 'border-emerald-500 bg-emerald-50'
                        : 'border-zinc-300 bg-white hover:border-zinc-400'
                    }`}
                  >
                    <span className="flex items-center gap-2">
                      <Icon
                        className={`size-4 shrink-0 ${
                          selected ? 'text-emerald-600' : 'text-zinc-500'
                        }`}
                        aria-hidden="true"
                      />
                      <span
                        className={`text-sm font-semibold ${
                          selected ? 'text-emerald-900' : 'text-zinc-800'
                        }`}
                      >
                        {option.name}
                      </span>
                    </span>
                    <span className="mt-1.5 block text-xs leading-5 text-zinc-500">
                      {option.summary}
                    </span>
                  </button>
                )
              })}
            </div>
            <p className="mt-2 text-xs leading-5 text-zinc-500">
              {jd.trim()
                ? '已使用你填写的 JD，岗位选择用于限定考察范围与真题来源。'
                : '未填写 JD 时，将使用该岗位的默认职责描述。'}
            </p>
          </div>
        )}

        <p
          className={`mt-3 flex items-start gap-2 rounded-lg border p-3 text-sm leading-5 ${
            resumeId
              ? 'border-emerald-200 bg-emerald-50 text-emerald-800'
              : 'border-amber-200 bg-amber-50 text-amber-800'
          }`}
        >
          <IdCard className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          <span>
            {resumeId
              ? `本场面试基于简历档案${resumeName ? `：${resumeName}` : ''}，提问会围绕其中的项目和量化数据展开。`
              : '尚未导入简历档案，面试官只能问通用问题。建议先在右侧导入简历。'}
          </span>
        </p>

        <div className="mt-4">
          <span className="field-label">提问数量</span>
          <div className="inline-flex rounded-lg border border-zinc-300 bg-zinc-100 p-1">
            {QUESTION_OPTIONS.map((count) => (
              <button
                key={count}
                type="button"
                onClick={() => setMaxQuestions(count)}
                className={`rounded-md px-4 py-1.5 text-sm font-semibold ${
                  maxQuestions === count
                    ? 'bg-white text-zinc-900 shadow-sm'
                    : 'text-zinc-500 hover:text-zinc-700'
                }`}
              >
                {count} 题
              </button>
            ))}
          </div>
        </div>

        <label className="mt-4 flex items-center gap-2 text-sm text-zinc-700">
          <input
            type="checkbox"
            className="size-4 rounded border-zinc-300 accent-emerald-600"
            checked={voiceMode}
            onChange={(event) => setVoiceMode(event.target.checked)}
          />
          语音模式（面试官朗读问题，你用麦克风回答）
        </label>

        {voiceMode && voices.length > 0 && (
          <div className="mt-4 rounded-lg border border-zinc-200 bg-zinc-50 p-4">
            <div className="flex flex-wrap items-end gap-3">
              <div className="min-w-0 flex-1">
                <label
                  className="block text-xs font-medium text-zinc-600"
                  htmlFor="interviewer-voice"
                >
                  面试官音色
                </label>
                <select
                  id="interviewer-voice"
                  className="mt-1.5 w-full rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-800"
                  value={voiceId}
                  onChange={(event) => setVoiceId(event.target.value)}
                >
                  {voices.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.label}
                    </option>
                  ))}
                </select>
              </div>
              <button
                type="button"
                className="secondary-btn px-3 py-2"
                onClick={handlePreviewVoice}
                disabled={previewing}
                title="试听当前音色"
              >
                {previewing ? (
                  <Loader2 className="size-4 animate-spin" aria-hidden="true" />
                ) : (
                  <Volume2 className="size-4" aria-hidden="true" />
                )}
                试听
              </button>
            </div>

            {activeVoice && (
              <p className="mt-2 text-xs text-zinc-500">{activeVoice.desc}</p>
            )}

            <div className="mt-3">
              <label
                className="block text-xs font-medium text-zinc-600"
                htmlFor="interviewer-rate"
              >
                语速 {speechRate > 0 ? `+${speechRate}` : speechRate}%
              </label>
              <input
                id="interviewer-rate"
                type="range"
                min={-30}
                max={30}
                step={5}
                value={speechRate}
                onChange={(event) => setSpeechRate(Number(event.target.value))}
                className="mt-1.5 w-full accent-emerald-600"
              />
            </div>
          </div>
        )}

        {!recognitionSupported && voiceMode && (
          <p className="mt-3 flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
            <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
            <span>当前浏览器不支持语音识别，可继续使用打字回答；建议改用 Chrome 或 Edge。</span>
          </p>
        )}

        <button type="button" className="primary-btn mt-5 w-full py-3" onClick={handleStart}>
          <Mic className="size-5" aria-hidden="true" />
          开始模拟面试
        </button>
      </section>
    )
  }

  return (
    <section className="tool-card">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <span
            className={`flex size-11 items-center justify-center rounded-lg text-white transition ${
              isSpeaking ? 'bg-emerald-600' : 'bg-zinc-400'
            }`}
          >
            <UserRound className="size-6" aria-hidden="true" />
          </span>
          <div>
            <p className="text-sm font-semibold text-zinc-900">AI 面试官</p>
            <p className="text-xs text-zinc-500">
              {isSpeaking
                ? '正在提问'
                : isThinking
                  ? '正在思考'
                  : isListening
                    ? '正在听你回答'
                    : isDone
                      ? '面试已结束'
                      : '等待你的回答'}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <span className="rounded-lg bg-zinc-100 px-2.5 py-1 text-xs font-medium text-zinc-600">
            {Math.min(askedCount, maxQuestions)} / {maxQuestions} 题
          </span>
          {usingFallbackVoice && (
            <span
              className="rounded-lg bg-amber-100 px-2.5 py-1 text-xs font-medium text-amber-800"
              title="神经语音暂时不可用，已回退到浏览器内置语音"
            >
              备用音色
            </span>
          )}
          {isSpeaking && (
            <button
              type="button"
              className="secondary-btn px-3 py-2"
              onClick={stopSpeaking}
              aria-label="停止朗读"
              title="停止朗读"
            >
              <VolumeX className="size-4" aria-hidden="true" />
            </button>
          )}
        </div>
      </div>

      <div
        ref={scrollRef}
        className="mt-4 max-h-96 space-y-3 overflow-y-auto rounded-lg border border-zinc-200 bg-zinc-50 p-4"
      >
        {messages.map((message, index) => (
          <div
            key={`${index}-${message.content.slice(0, 12)}`}
            className={`flex ${message.role === 'candidate' ? 'justify-end' : 'justify-start'}`}
          >
            <p
              className={`max-w-[85%] rounded-lg px-3 py-2 text-sm leading-6 ${
                message.role === 'candidate'
                  ? 'bg-emerald-600 text-white'
                  : 'bg-white text-zinc-800 shadow-sm'
              }`}
            >
              {message.content}
            </p>
          </div>
        ))}

        {interimText && (
          <div className="flex justify-end">
            <p className="max-w-[85%] rounded-lg border border-dashed border-emerald-300 bg-white px-3 py-2 text-sm leading-6 text-zinc-500">
              {interimText}
            </p>
          </div>
        )}

        {isThinking && (
          <div className="flex items-center gap-2 text-sm text-zinc-500">
            <Loader2 className="size-4 animate-spin" aria-hidden="true" />
            面试官正在思考
          </div>
        )}
      </div>

      {activeError && (
        <p className="mt-3 flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          <span>{activeError}</span>
        </p>
      )}

      {!isDone && (
        <div className="mt-4 space-y-3">
          {voiceMode && recognitionSupported ? (
            <div className="flex flex-wrap items-center gap-3">
              {isListening ? (
                <button
                  type="button"
                  className="primary-btn"
                  onClick={handleVoiceSubmit}
                  disabled={isThinking}
                >
                  <Square className="size-4" aria-hidden="true" />
                  回答完毕
                </button>
              ) : (
                <button
                  type="button"
                  className="secondary-btn"
                  onClick={startListening}
                  disabled={isThinking || isSpeaking}
                >
                  <Mic className="size-4" aria-hidden="true" />
                  开始回答
                </button>
              )}
              <span className="flex items-center gap-1.5 text-xs text-zinc-500">
                {isListening ? (
                  <>
                    <Mic className="size-3.5 text-red-500" aria-hidden="true" />
                    麦克风开启中
                  </>
                ) : (
                  <>
                    <MicOff className="size-3.5" aria-hidden="true" />
                    麦克风已关闭
                  </>
                )}
                {isSpeaking && (
                  <>
                    <Volume2 className="ml-2 size-3.5 text-emerald-600" aria-hidden="true" />
                    面试官正在说话
                  </>
                )}
              </span>
            </div>
          ) : (
            <div className="flex items-end gap-2">
              <textarea
                className="text-input min-h-20"
                value={typedAnswer}
                onChange={(event) => setTypedAnswer(event.target.value)}
                placeholder="输入你的回答"
              />
              <button
                type="button"
                className="primary-btn shrink-0"
                onClick={() => submitAnswer(typedAnswer)}
                disabled={!typedAnswer.trim() || isThinking}
              >
                <Send className="size-4" aria-hidden="true" />
                提交
              </button>
            </div>
          )}
        </div>
      )}

      <div className="mt-4 flex flex-wrap gap-3 border-t border-zinc-100 pt-4">
        <button
          type="button"
          className="primary-btn"
          onClick={handleFinish}
          disabled={messages.length < 2}
        >
          <CheckCheck className="size-4" aria-hidden="true" />
          {isDone ? '生成面试评分报告' : '结束并生成评分'}
        </button>
      </div>
    </section>
  )
}
