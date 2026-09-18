import {
  AlertTriangle,
  AudioLines,
  BrainCircuit,
  ClipboardPaste,
  FileText,
  GraduationCap,
  Loader2,
  Mic,
  MessagesSquare,
  NotebookPen,
  Play,
  RotateCcw,
} from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import {
  analyze,
  createTranscriptionJob,
  getActiveResume,
  getQuizProgress,
  listKnowledgeDocuments,
  listMistakes,
  pollJob,
  searchKnowledge,
} from './api'
import { ChunkedUploader } from './components/ChunkedUploader'
import { ExpressionDrill } from './components/ExpressionDrill'
import { KnowledgeLibrary } from './components/KnowledgeLibrary'
import { MistakeBook } from './components/MistakeBook'
import { MockInterview } from './components/MockInterview'
import { QuizResult } from './components/QuizResult'
import { QuizRunner } from './components/QuizRunner'
import { QuizSetup } from './components/QuizSetup'
import { ReportView } from './components/ReportView'
import { ResumeManager } from './components/ResumeManager'
import { ResumeAdvisor } from './components/ResumeAdvisor'
import { Stepper } from './components/Stepper'
import type {
  AnalysisReport,
  JobState,
  KnowledgeDocument,
  MistakeItem,
  QuizAttempt,
  QuizPaper,
  QuizProgress,
  ResumeRecord,
} from './types'

type Phase = 'input' | 'analyzing' | 'report'
type InputMode = 'upload' | 'paste' | 'live'
type WorkspaceView = 'practice' | 'quiz' | 'expression'
type QuizPhase = 'setup' | 'running' | 'result'

interface UploadedFile {
  id: string
  name: string
  size: number
}

function formatBytes(bytes: number): string {
  if (bytes >= 1024 * 1024 * 1024) {
    return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`
  }
  if (bytes >= 1024 * 1024) {
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`
  }
  return `${Math.max(1, Math.round(bytes / 1024))} KB`
}

export default function App() {
  const [phase, setPhase] = useState<Phase>('input')
  const [inputMode, setInputMode] = useState<InputMode>('upload')
  const [uploadedFile, setUploadedFile] = useState<UploadedFile | null>(null)
  const [job, setJob] = useState<JobState | null>(null)
  const [transcript, setTranscript] = useState('')
  const [jd, setJd] = useState(() => window.localStorage.getItem('opc-jd') ?? '')
  const [resume, setResume] = useState(
    () => window.localStorage.getItem('opc-resume') ?? '',
  )
  const [report, setReport] = useState<AnalysisReport | null>(null)
  const [error, setError] = useState('')
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([])
  const [knowledgeChunkCount, setKnowledgeChunkCount] = useState(0)
  const [activeResume, setActiveResume] = useState<ResumeRecord | null>(null)
  const [view, setView] = useState<WorkspaceView>('practice')
  const [quizPhase, setQuizPhase] = useState<QuizPhase>('setup')
  const [paper, setPaper] = useState<QuizPaper | null>(null)
  const [attempt, setAttempt] = useState<QuizAttempt | null>(null)
  const [mistakes, setMistakes] = useState<MistakeItem[]>([])
  const [progress, setProgress] = useState<QuizProgress | null>(null)

  useEffect(() => {
    window.localStorage.setItem('opc-jd', jd)
  }, [jd])

  useEffect(() => {
    window.localStorage.setItem('opc-resume', resume)
  }, [resume])

  useEffect(() => {
    listKnowledgeDocuments()
      .then(setDocuments)
      .catch(() => setDocuments([]))
  }, [])

  useEffect(() => {
    getActiveResume()
      .then(setActiveResume)
      .catch(() => setActiveResume(null))
  }, [])

  const refreshQuizState = useCallback(async () => {
    const [nextMistakes, nextProgress] = await Promise.all([
      listMistakes().catch(() => [] as MistakeItem[]),
      getQuizProgress().catch(() => null),
    ])
    setMistakes(nextMistakes)
    setProgress(nextProgress)
  }, [])

  useEffect(() => {
    void refreshQuizState()
  }, [refreshQuizState])

  const handlePaperReady = useCallback((next: QuizPaper) => {
    setPaper(next)
    setAttempt(null)
    setQuizPhase('running')
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }, [])

  const handleGraded = useCallback(
    async (next: QuizAttempt) => {
      setAttempt(next)
      setQuizPhase('result')
      await refreshQuizState()
      window.scrollTo({ top: 0, behavior: 'smooth' })
    },
    [refreshQuizState],
  )

  const handleUploaded = useCallback(async (fileId: string, file: File) => {
    setUploadedFile({ id: fileId, name: file.name, size: file.size })
    setJob(null)
    setTranscript('')
    setError('')
    try {
      const created = await createTranscriptionJob(fileId)
      setJob({
        job_id: created.job_id,
        file_id: fileId,
        status: 'queued',
        stage: '排队中',
        progress: 0,
      })
      const finished = await pollJob(created.job_id, setJob)
      setTranscript(finished.result?.text ?? '')
    } catch (transcribeError) {
      setError(
        transcribeError instanceof Error ? transcribeError.message : '转写失败，请重试。',
      )
    }
  }, [])

  const isTranscribing = job?.status === 'queued' || job?.status === 'running'
  const canAnalyze = Boolean(transcript.trim()) && !isTranscribing && phase === 'input'

  const startAnalysis = async (overrideTranscript?: string) => {
    const source = (overrideTranscript ?? transcript).trim()
    if (!source) return
    setPhase('analyzing')
    setError('')
    setKnowledgeChunkCount(0)
    try {
      let knowledge = ''
      if (documents.length > 0) {
        try {
          const query = `${jd}\n${resume}\n${source.slice(0, 1800)}`.trim()
          const chunks = await searchKnowledge(query || source.slice(0, 1800), 6)
          setKnowledgeChunkCount(chunks.length)
          knowledge = chunks
            .map(
              (chunk, index) =>
                `[资料 ${index + 1}] ${chunk.title}\n${chunk.text}`,
            )
            .join('\n\n')
        } catch {
          knowledge = ''
        }
      }
      const result = await analyze({
        transcript: source,
        jd,
        resume,
        knowledge,
        resume_id: activeResume?.resume_id,
      })
      setReport(result)
      setPhase('report')
    } catch (analysisError) {
      setError(analysisError instanceof Error ? analysisError.message : 'AI 分析失败，请重试。')
      setPhase('input')
    }
  }

  const handleInterviewFinished = useCallback(
    (interviewTranscript: string) => {
      if (!interviewTranscript.trim()) {
        setError('本场面试还没有可分析的对话内容。')
        return
      }
      setTranscript(interviewTranscript)
      void startAnalysis(interviewTranscript)
    },
    // startAnalysis 依赖的状态在同一次渲染内已是最新值。
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [jd, resume, documents, activeResume],
  )

  const restart = () => {
    setPhase('input')
    setInputMode('upload')
    setUploadedFile(null)
    setJob(null)
    setTranscript('')
    setReport(null)
    setError('')
  }

  const activeStep =
    phase === 'report' ? 3 : phase === 'analyzing' ? 2 : isTranscribing ? 1 : 0
  const jobProgress = job?.progress ? Math.round(job.progress * 100) : 0

  return (
    <div className="min-h-screen bg-zinc-100">
      <header className="border-b border-zinc-200 bg-white">
        <div
          className={`mx-auto flex items-center justify-between gap-4 py-4 ${
            view === 'quiz' ? 'max-w-[1800px] px-3' : 'max-w-6xl px-4'
          }`}
        >
          <div className="flex items-center gap-3">
            <span className="flex size-10 items-center justify-center rounded-lg bg-emerald-600 text-white">
              <BrainCircuit className="size-5" aria-hidden="true" />
            </span>
            <div>
              <h1 className="text-lg font-bold text-zinc-900">面试练习助手</h1>
              <p className="text-xs text-zinc-500">录音复盘 · 逐题评分 · 知识库引用</p>
            </div>
          </div>
          <div className="flex flex-wrap items-center justify-end gap-3">
            <div className="inline-flex rounded-lg border border-zinc-300 bg-zinc-100 p-1">
              <button
                type="button"
                className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition ${
                  view === 'practice'
                    ? 'bg-white text-zinc-900 shadow-sm'
                    : 'text-zinc-500 hover:text-zinc-700'
                }`}
                onClick={() => setView('practice')}
              >
                <MessagesSquare className="size-4" aria-hidden="true" />
                面试练习
              </button>
              <button
                type="button"
                className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition ${
                  view === 'quiz'
                    ? 'bg-white text-zinc-900 shadow-sm'
                    : 'text-zinc-500 hover:text-zinc-700'
                }`}
                onClick={() => setView('quiz')}
              >
                <GraduationCap className="size-4" aria-hidden="true" />
                在线刷题
              </button>
              <button
                type="button"
                className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition ${
                  view === 'expression'
                    ? 'bg-white text-zinc-900 shadow-sm'
                    : 'text-zinc-500 hover:text-zinc-700'
                }`}
                onClick={() => setView('expression')}
              >
                <Mic className="size-4" aria-hidden="true" />
                表达训练
              </button>
            </div>
            {view === 'practice' && (
              <button type="button" className="secondary-btn" onClick={restart}>
                <RotateCcw className="size-4" aria-hidden="true" />
                重新开始
              </button>
            )}
          </div>
        </div>
      </header>

      <main
        className={`mx-auto space-y-5 py-6 ${
          view === 'quiz' ? 'max-w-[1800px] px-3' : 'max-w-6xl px-4'
        }`}
      >
        {view === 'practice' && <Stepper current={activeStep} />}

        {view === 'practice' && error && (
          <div className="flex items-start gap-3 rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
            <AlertTriangle className="mt-0.5 size-5 shrink-0" aria-hidden="true" />
            <div>
              <p className="font-semibold">处理中断</p>
              <p className="mt-1">{error}</p>
              {job?.status === 'error' && (
                <p className="mt-1 text-xs text-red-600">
                  可以改用“粘贴转写”模式继续完成 AI 分析。
                </p>
              )}
            </div>
          </div>
        )}

        {view === 'quiz' && (
          <>
            <div className="grid gap-5 xl:grid-cols-[260px_minmax(0,1fr)_300px]">
            <aside className="order-3 min-w-0 xl:order-1">
              <KnowledgeLibrary documents={documents} onDocumentsChange={setDocuments} />
            </aside>

            <div className="order-1 min-w-0 space-y-5 xl:order-2">
              <p className="flex items-start gap-2 rounded-lg border border-zinc-200 bg-white px-3 py-2.5 text-xs leading-5 text-zinc-500">
                <NotebookPen className="mt-0.5 size-3.5 shrink-0 text-zinc-400" aria-hidden="true" />
                <span>
                  考题依据知识库原文命题，客观题在本地判分；错题会自动进入错题本。
                </span>
              </p>
              {quizPhase === 'setup' && (
                <QuizSetup
                  documents={documents}
                  mistakeCount={mistakes.length}
                  progress={progress}
                  onPaperReady={handlePaperReady}
                />
              )}
              {quizPhase === 'running' && paper && (
                <QuizRunner
                  paper={paper}
                  onGraded={handleGraded}
                  onCancel={() => setQuizPhase('setup')}
                />
              )}
              {quizPhase === 'result' && attempt && (
                <QuizResult
                  attempt={attempt}
                  cumulativeMistakeCount={mistakes.length}
                  onRetryMistakes={handlePaperReady}
                  onBackToSetup={() => setQuizPhase('setup')}
                />
              )}
            </div>

            <aside className="order-2 min-w-0 space-y-5 xl:order-3">
              <MistakeBook
                mistakes={mistakes}
                progress={progress}
                onMistakesChange={setMistakes}
              />
            </aside>
          </div>
          </>
        )}

        {view === 'expression' && (
          <div className="mx-auto w-full max-w-3xl">
            <ExpressionDrill />
          </div>
        )}

        {view === 'practice' && phase === 'input' && (
          <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_380px]">
            <section className="tool-card min-w-0">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <h2 className="text-base font-semibold text-zinc-900">面试材料</h2>
                <div className="flex flex-wrap rounded-lg border border-zinc-300 bg-zinc-100 p-1">
                  <button
                    type="button"
                    className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium ${
                      inputMode === 'live'
                        ? 'bg-white text-zinc-900 shadow-sm'
                        : 'text-zinc-500'
                    }`}
                    onClick={() => setInputMode('live')}
                  >
                    <MessagesSquare className="size-4" aria-hidden="true" />
                    模拟面试
                  </button>
                  <button
                    type="button"
                    className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium ${
                      inputMode === 'upload'
                        ? 'bg-white text-zinc-900 shadow-sm'
                        : 'text-zinc-500'
                    }`}
                    onClick={() => setInputMode('upload')}
                  >
                    <AudioLines className="size-4" aria-hidden="true" />
                    录音上传
                  </button>
                  <button
                    type="button"
                    className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium ${
                      inputMode === 'paste'
                        ? 'bg-white text-zinc-900 shadow-sm'
                        : 'text-zinc-500'
                    }`}
                    onClick={() => setInputMode('paste')}
                  >
                    <ClipboardPaste className="size-4" aria-hidden="true" />
                    粘贴转写
                  </button>
                </div>
              </div>

              <div className="mt-5">
                {inputMode === 'live' ? (
                  <MockInterview
                    jd={jd}
                    resume={resume}
                    resumeId={activeResume?.resume_id}
                    resumeName={activeResume?.profile?.name}
                    hasKnowledge={documents.length > 0}
                    onFinished={handleInterviewFinished}
                  />
                ) : inputMode === 'upload' ? (
                  <ChunkedUploader onUploaded={handleUploaded} />
                ) : (
                  <div>
                    <label htmlFor="transcript" className="field-label">
                      转写文本
                    </label>
                    <textarea
                      id="transcript"
                      className="text-input min-h-64 font-mono text-xs leading-6"
                      value={transcript}
                      onChange={(event) => setTranscript(event.target.value)}
                      placeholder="粘贴已有转写文本。建议保留提问和回答顺序，分析会更准确。"
                    />
                  </div>
                )}
              </div>

              {inputMode === 'upload' && job && (
                <div className="mt-5 rounded-lg border border-zinc-200 bg-zinc-50 p-4">
                  <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
                    <span className="font-medium text-zinc-700">
                      {job.stage ?? '处理中'}
                    </span>
                    <span className="text-zinc-500">{jobProgress}%</span>
                  </div>
                  <div className="mt-3 h-2 overflow-hidden rounded-full bg-zinc-200">
                    <div
                      className={`h-full rounded-full ${
                        job.status === 'error' ? 'bg-red-500' : 'bg-emerald-600'
                      }`}
                      style={{ width: `${job.status === 'error' ? 100 : jobProgress}%` }}
                    />
                  </div>
                  {job.result && (
                    <dl className="mt-3 grid grid-cols-2 gap-2 text-xs text-zinc-500 sm:grid-cols-4">
                      <div>
                        <dt className="font-medium text-zinc-700">时长</dt>
                        <dd>{Math.round(job.result.duration / 60)} 分钟</dd>
                      </div>
                      <div>
                        <dt className="font-medium text-zinc-700">原始大小</dt>
                        <dd>{formatBytes(job.result.original_size)}</dd>
                      </div>
                      <div>
                        <dt className="font-medium text-zinc-700">压缩后</dt>
                        <dd>{formatBytes(job.result.compressed_size)}</dd>
                      </div>
                      <div>
                        <dt className="font-medium text-zinc-700">识别分片</dt>
                        <dd>{job.result.segment_count}</dd>
                      </div>
                    </dl>
                  )}
                </div>
              )}

              {inputMode === 'upload' && transcript && (
                <div className="mt-5">
                  <label htmlFor="generated-transcript" className="field-label">
                    转写结果
                  </label>
                  <textarea
                    id="generated-transcript"
                    className="text-input min-h-40 font-mono text-xs leading-6"
                    value={transcript}
                    onChange={(event) => setTranscript(event.target.value)}
                  />
                </div>
              )}
            </section>

            <aside className="min-w-0 space-y-5">
              <ResumeManager
                activeResume={activeResume}
                onActiveResumeChange={setActiveResume}
              />

              <ResumeAdvisor activeResume={activeResume} />

              <section className="tool-card">
                <label htmlFor="jd" className="field-label">
                  目标岗位 JD
                </label>
                <textarea
                  id="jd"
                  className="text-input min-h-44"
                  value={jd}
                  onChange={(event) => setJd(event.target.value)}
                  placeholder="粘贴岗位职责和任职要求，用于生成更贴合的追问。"
                />
              </section>

              <section className="tool-card">
                <label htmlFor="resume" className="field-label">
                  补充说明
                </label>
                <textarea
                  id="resume"
                  className="text-input min-h-28"
                  value={resume}
                  onChange={(event) => setResume(event.target.value)}
                  placeholder="简历之外想补充的信息，例如近期学习方向、想重点被追问的项目。"
                />
                {!activeResume && (
                  <p className="mt-2 text-xs leading-5 text-amber-700">
                    还没有导入简历档案，面试官只能依据这里的文字提问。
                  </p>
                )}
              </section>

              <KnowledgeLibrary documents={documents} onDocumentsChange={setDocuments} />

              {inputMode !== 'live' && (
                <button
                  type="button"
                  className="primary-btn w-full py-3 text-base"
                  onClick={() => void startAnalysis()}
                  disabled={!canAnalyze}
                >
                  <Play className="size-5" aria-hidden="true" />
                  开始 AI 分析
                </button>
              )}
              {uploadedFile && (
                <p className="text-center text-xs text-zinc-500">
                  当前文件：{uploadedFile.name}（{formatBytes(uploadedFile.size)}）
                </p>
              )}
            </aside>
          </div>
        )}

        {view === 'practice' && phase === 'analyzing' && (
          <section className="tool-card flex flex-col items-center justify-center gap-4 py-16 text-center">
            <Loader2 className="size-10 animate-spin text-emerald-600" aria-hidden="true" />
            <div>
              <h2 className="text-lg font-semibold text-zinc-900">AI 正在复盘</h2>
              <p className="mt-2 text-sm text-zinc-500">
                正在识别问题、评估五个维度，并生成下一轮追问。
                {knowledgeChunkCount > 0
                  ? ` 已引用 ${knowledgeChunkCount} 个知识库片段。`
                  : ''}
              </p>
            </div>
          </section>
        )}

        {view === 'practice' && phase === 'report' && report && (
          <ReportView report={report} onRestart={restart} />
        )}
      </main>

      <footer className="border-t border-zinc-200 bg-white py-4">
        <p className="mx-auto flex max-w-6xl items-center justify-center gap-2 px-4 text-center text-xs text-zinc-500">
          <FileText className="size-4 shrink-0" aria-hidden="true" />
          录音、转写和知识库保存在本地 backend/data；转写和分析调用你配置的 API。
        </p>
      </footer>
    </div>
  )
}
