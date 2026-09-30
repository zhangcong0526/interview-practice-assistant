import {
  AlertTriangle,
  ArrowRight,
  BookMarked,
  CheckCircle2,
  GitBranch,
  Lightbulb,
  Loader2,
  Mic,
  RotateCcw,
  Target,
  XCircle,
} from 'lucide-react'
import { useState } from 'react'
import { generateMistakePaper } from '../api'
import type {
  MasteryLevel,
  MistakeQuizScope,
  QuizAttempt,
  QuizPaper,
} from '../types'

interface QuizResultProps {
  attempt: QuizAttempt
  cumulativeMistakeCount: number
  onRetryMistakes: (paper: QuizPaper) => void
  onBackToSetup: () => void
  onOpenExpression: (
    topic: string,
    module?: string,
    sourceDocIds?: string[],
    sourceTag?: string,
  ) => void
}

const LEVEL_LABEL: Record<MasteryLevel, string> = {
  beginner: '刚入门',
  developing: '进步中',
  proficient: '较熟练',
  mastered: '已掌握',
}

const TYPE_LABEL: Record<string, string> = {
  single: '单选题',
  multiple: '多选题',
  judge: '判断题',
}

const QUESTION_TYPE_ORDER = ['single', 'multiple', 'judge'] as const

export function QuizResult({
  attempt,
  cumulativeMistakeCount,
  onRetryMistakes,
  onBackToSetup,
  onOpenExpression,
}: QuizResultProps) {
  const [busy, setBusy] = useState(false)
  const [topicBusy, setTopicBusy] = useState('')
  const [error, setError] = useState('')
  const [mistakeScope, setMistakeScope] = useState<MistakeQuizScope>('current')
  const { review } = attempt
  const guide = review.learning_guide
  const wrongQuestions = attempt.questions.filter((item) => !item.is_correct)
  const attemptDocIds = Array.from(
    new Set(
      attempt.questions
        .map((item) => item.source_doc_id)
        .filter((id): id is string => Boolean(id)),
    ),
  )
  const currentTypeStats = QUESTION_TYPE_ORDER.map((type) => {
    const questions = attempt.questions.filter((item) => item.type === type)
    const correct = questions.filter((item) => item.is_correct).length
    return {
      type,
      label: TYPE_LABEL[type],
      total: questions.length,
      correct,
      accuracy: questions.length > 0 ? correct / questions.length : 0,
    }
  }).filter((stat) => stat.total > 0)
  const currentMistakeCount = wrongQuestions.length
  const retryDisabled =
    busy ||
    (mistakeScope === 'current'
      ? currentMistakeCount === 0
      : cumulativeMistakeCount === 0)

  const handleRetry = async () => {
    if (retryDisabled) return
    setBusy(true)
    setError('')
    try {
      onRetryMistakes(
        await generateMistakePaper({
          scope: mistakeScope,
          attempt_id: mistakeScope === 'current' ? attempt.attempt_id : '',
          limit: 8,
          difficulty: 'mixed',
          time_range: 'all',
          total: mistakeScope === 'all' ? 20 : undefined,
        }),
      )
    } catch (retryError) {
      setError(retryError instanceof Error ? retryError.message : '错题重练组卷失败。')
      setBusy(false)
    }
  }

  const handleTopicRedo = async (topic: string) => {
    if (topicBusy) return
    setTopicBusy(topic)
    setError('')
    try {
      onRetryMistakes(
        await generateMistakePaper({
          scope: 'all',
          limit: 1,
          difficulty: 'mixed',
          time_range: 'all',
          topics: [topic],
          total: 5,
        }),
      )
    } catch (redoError) {
      setError(redoError instanceof Error ? redoError.message : '知识点重练组卷失败。')
    } finally {
      setTopicBusy('')
    }
  }

  const handleConsolidate = () => {
    const topic = guide?.focus_topics[0]?.topic
    if (topic) {
      void handleTopicRedo(topic)
    } else {
      void handleRetry()
    }
  }

  return (
    <div className="space-y-5">
      <section className="tool-card">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <p className="text-xs font-medium text-zinc-500">{attempt.paper_title}</p>
            <p className="mt-1 flex items-baseline gap-2">
              <span className="text-4xl font-bold text-zinc-900">{attempt.score}</span>
              <span className="text-sm text-zinc-500">
                分 · 答对 {attempt.correct_count} / {attempt.total}
              </span>
            </p>
          </div>
          <span
            className={`rounded-lg px-3 py-1.5 text-sm font-semibold ${
              review.mastery_level === 'mastered' || review.mastery_level === 'proficient'
                ? 'bg-emerald-100 text-emerald-800'
                : 'bg-amber-100 text-amber-800'
            }`}
          >
            {LEVEL_LABEL[review.mastery_level]}
          </span>
        </div>

        {review.summary && (
          <p className="mt-4 text-sm leading-6 text-zinc-700">{review.summary}</p>
        )}

        <div
          className={`mt-4 flex items-start gap-2.5 rounded-lg border p-3.5 ${
            review.can_advance
              ? 'border-emerald-200 bg-emerald-50'
              : 'border-amber-200 bg-amber-50'
          }`}
        >
          {review.can_advance ? (
            <ArrowRight className="mt-0.5 size-4 shrink-0 text-emerald-700" aria-hidden="true" />
          ) : (
            <Target className="mt-0.5 size-4 shrink-0 text-amber-700" aria-hidden="true" />
          )}
          <div className="min-w-0">
            <p
              className={`text-sm font-semibold ${
                review.can_advance ? 'text-emerald-800' : 'text-amber-800'
              }`}
            >
              {review.can_advance ? '可以进入下一个板块了' : '建议先巩固当前板块'}
            </p>
            {review.advance_reason && (
              <p
                className={`mt-1 text-sm leading-6 ${
                  review.can_advance ? 'text-emerald-800' : 'text-amber-800'
                }`}
              >
                {review.advance_reason}
              </p>
            )}
          </div>
          {!review.can_advance && (
            <button
              type="button"
              className="primary-btn shrink-0 px-3 py-2 text-xs"
              onClick={handleConsolidate}
              disabled={busy || Boolean(topicBusy)}
            >
              {topicBusy ? (
                <Loader2 className="size-3.5 animate-spin" aria-hidden="true" />
              ) : (
                <Target className="size-3.5" aria-hidden="true" />
              )}
              开始巩固
            </button>
          )}
        </div>

        {guide && (
          <section className="mt-4 rounded-lg border border-zinc-200 bg-zinc-50 p-3.5">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h3 className="text-sm font-semibold text-zinc-900">学习指引</h3>
              <span
                className={`rounded-full px-2.5 py-1 text-xs font-semibold ${
                  guide.interview_ready
                    ? 'bg-emerald-100 text-emerald-800'
                    : 'bg-amber-100 text-amber-800'
                }`}
              >
                {guide.interview_ready ? '建议进入模拟面试' : '建议继续刷题巩固'}
              </span>
            </div>
            <p className="mt-2 text-sm leading-6 text-zinc-700">{guide.summary}</p>

            <p className="mt-3 text-xs font-semibold text-zinc-700">本次题型表现</p>
            <div className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-3">
              {currentTypeStats.map((stat) => (
                <div key={stat.type} className="rounded-lg bg-white px-3 py-2 ring-1 ring-zinc-200">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-xs font-medium text-zinc-600">{stat.label}</span>
                    <span
                      className={`text-xs font-semibold ${
                        stat.accuracy >= 0.85
                          ? 'text-emerald-700'
                          : stat.accuracy >= 0.6
                            ? 'text-amber-700'
                            : 'text-red-700'
                      }`}
                    >
                      {stat.total > 0 ? `${Math.round(stat.accuracy * 100)}%` : '未练习'}
                    </span>
                  </div>
                  <p className="mt-1 text-xs text-zinc-500">
                    答对 {stat.correct}/{stat.total}
                  </p>
                </div>
              ))}
            </div>

            <div className="mt-3">
              <p className="text-xs font-semibold text-zinc-700">下一步重点</p>
              {guide.focus_scope_note && (
                <p className="mt-1 text-xs leading-5 text-zinc-500">{guide.focus_scope_note}</p>
              )}
              {guide.focus_topics.length > 0 ? (
                <ul className="mt-1.5 space-y-2">
                  {guide.focus_topics.slice(0, 5).map((topic) => (
                    <li key={topic.topic} className="rounded-lg bg-white px-3 py-2 ring-1 ring-zinc-200">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <span className="text-xs font-semibold text-zinc-800">{topic.topic}</span>
                        <span className="text-xs text-zinc-500">{topic.module}</span>
                      </div>
                      <div className="mt-1.5 flex flex-wrap gap-1.5">
                        <span className="rounded bg-zinc-100 px-1.5 py-0.5 text-[11px] text-zinc-600">
                          历史 {topic.history_stats?.correct ?? topic.correct}/{topic.history_stats?.total ?? topic.total}
                          {' · '}
                          正确率 {Math.round((topic.history_stats?.accuracy ?? topic.accuracy) * 100)}%
                        </span>
                        {topic.current_stats && (
                          <span className="rounded bg-amber-50 px-1.5 py-0.5 text-[11px] text-amber-700">
                            本次 {topic.current_stats.correct}/{topic.current_stats.total}
                          </span>
                        )}
                        {(topic.type_gaps ?? []).slice(0, 2).map((gap) => (
                          <span
                            key={gap.type}
                            className={`rounded px-1.5 py-0.5 text-[11px] ${
                              gap.status === 'unpracticed'
                                ? 'bg-sky-50 text-sky-700'
                                : 'bg-red-50 text-red-700'
                            }`}
                          >
                            {gap.label}
                            {gap.status === 'unpracticed' ? '未练' : ` ${gap.correct}/${gap.total}`}
                          </span>
                        ))}
                      </div>
                      <p className="mt-1.5 text-xs leading-5 text-zinc-600">
                        为什么优先：{topic.reasons.join('；')}
                      </p>
                      {(topic.source_titles ?? []).length > 0 && (
                        <p className="mt-1 text-xs leading-5 text-zinc-500">
                          补读来源：{(topic.source_titles ?? []).join('、')}
                        </p>
                      )}
                      {topic.practice_plan && topic.practice_plan.length > 0 ? (
                        <ol className="mt-1.5 space-y-1 text-xs leading-5 text-emerald-800">
                          {topic.practice_plan.map((step, index) => (
                            <li key={`${topic.topic}-step-${index}`} className="flex gap-1.5">
                              <span className="shrink-0 font-semibold">{index + 1}.</span>
                              <span>{step}</span>
                            </li>
                          ))}
                        </ol>
                      ) : (
                        <p className="mt-1 text-xs leading-5 text-emerald-800">
                          {topic.recommended_action}
                        </p>
                      )}
                      <div className="mt-2.5 flex flex-wrap gap-2">
                        <button
                          type="button"
                          className="inline-flex items-center gap-1 rounded-md bg-emerald-600 px-2 py-1 text-[11px] font-semibold text-white transition hover:bg-emerald-700 disabled:opacity-60"
                          onClick={() => void handleTopicRedo(topic.topic)}
                          disabled={Boolean(topicBusy)}
                        >
                          {topicBusy === topic.topic ? (
                            <Loader2 className="size-3 animate-spin" aria-hidden="true" />
                          ) : (
                            <RotateCcw className="size-3" aria-hidden="true" />
                          )}
                          去重做
                        </button>
                        <button
                          type="button"
                          className="inline-flex items-center gap-1 rounded-md bg-sky-600 px-2 py-1 text-[11px] font-semibold text-white transition hover:bg-sky-700 disabled:opacity-60"
                          onClick={() =>
                            onOpenExpression(topic.topic, topic.module, attemptDocIds, 'result_page')
                          }
                          disabled={Boolean(topicBusy)}
                        >
                          <Mic className="size-3" aria-hidden="true" />
                          去说一说
                        </button>
                      </div>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="mt-1 text-xs leading-5 text-zinc-500">
                  {guide.focus_scope_note
                    ? "这份试卷没有发现新的薄弱知识点。不用做专项补救，过一段时间再做一套同类题，确认不是靠短期记忆答对的。"
                    : "当前知识点掌握稳定，可以做一套混合卷保持手感。"}
                </p>
              )}
            </div>

            {!guide.interview_ready && guide.interview_reasons.length > 0 && (
              <p className="mt-2 text-xs leading-5 text-amber-800">
                模拟面试暂不建议跳转：{guide.interview_reasons.join('；')}。{guide.scope_note}
              </p>
            )}
          </section>
        )}

        {review.review_error && (
          <p className="mt-3 flex items-start gap-2 text-xs leading-5 text-amber-700">
            <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
            <span>{review.review_error}（得分为本地判分，不受影响）</span>
          </p>
        )}

        <div className="mt-4 flex flex-wrap items-center gap-3 border-t border-zinc-100 pt-4">
          <div className="inline-flex rounded-lg border border-zinc-300 bg-zinc-100 p-0.5">
            <button
              type="button"
              className={`rounded-md px-2.5 py-1.5 text-xs font-medium transition disabled:opacity-50 ${
                mistakeScope === 'current'
                  ? 'bg-white text-zinc-900 shadow-sm'
                  : 'text-zinc-500 hover:text-zinc-700'
              }`}
              onClick={() => setMistakeScope('current')}
              disabled={currentMistakeCount === 0}
              aria-pressed={mistakeScope === 'current'}
            >
              本次错题（{currentMistakeCount}）
            </button>
            <button
              type="button"
              className={`rounded-md px-2.5 py-1.5 text-xs font-medium transition disabled:opacity-50 ${
                mistakeScope === 'all'
                  ? 'bg-white text-zinc-900 shadow-sm'
                  : 'text-zinc-500 hover:text-zinc-700'
              }`}
              onClick={() => setMistakeScope('all')}
              disabled={cumulativeMistakeCount === 0}
              aria-pressed={mistakeScope === 'all'}
            >
              累计错题（{cumulativeMistakeCount}）
            </button>
          </div>
          <button type="button" className="primary-btn" onClick={handleRetry} disabled={retryDisabled}>
            {busy ? (
              <Loader2 className="size-4 animate-spin" aria-hidden="true" />
            ) : (
              <RotateCcw className="size-4" aria-hidden="true" />
            )}
            {mistakeScope === 'current' ? '本次错题变形练' : '累计错题重练'}
          </button>
          <button type="button" className="secondary-btn" onClick={onBackToSetup}>
            <BookMarked className="size-4" aria-hidden="true" />
            换知识点组卷
          </button>
        </div>
        <div className="mt-3 flex flex-wrap items-center justify-between gap-2 rounded-lg bg-emerald-50 px-3 py-2.5 text-xs text-emerald-800">
          <span>
            {attempt.review_sync_error
              ? '本次错题复盘同步未完成，可在错题复盘页重试；本地分数和答题记录已保存。'
              : '本次错题与薄弱知识点已同步到错题复盘。'}
          </span>
        </div>
        {error && <p className="mt-3 text-sm leading-5 text-red-600">{error}</p>}
      </section>

      {review.weak_topics.length > 0 && (
        <section className="tool-card">
          <h3 className="text-base font-semibold text-zinc-900">错因分析与快速理解</h3>
          <ul className="mt-4 space-y-4">
            {review.weak_topics.map((topic) => (
              <li key={topic.topic} className="border-t border-zinc-100 pt-4 first:border-0 first:pt-0">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <p className="text-sm font-semibold text-zinc-900">{topic.topic}</p>
                  <button
                    type="button"
                    className="inline-flex items-center gap-1 rounded-md bg-sky-600 px-2 py-1 text-[11px] font-semibold text-white transition hover:bg-sky-700"
                    onClick={() =>
                      onOpenExpression(topic.topic, topic.module, attemptDocIds, 'result_page')
                    }
                  >
                    <Mic className="size-3" aria-hidden="true" />
                    去说一说
                  </button>
                </div>
                {topic.diagnosis && (
                  <div className="mt-2.5 rounded-lg border border-amber-200 bg-amber-50 p-3">
                    <p className="text-xs font-semibold text-amber-800">为什么做错</p>
                    <p className="mt-1 text-sm leading-6 text-amber-900">{topic.diagnosis}</p>
                  </div>
                )}
                {topic.plain_summary && (
                  <div className="mt-2.5 rounded-lg border border-emerald-100 bg-emerald-50 p-3">
                    <p className="flex items-center gap-1.5 text-xs font-semibold text-emerald-800">
                      <Lightbulb className="size-3.5" aria-hidden="true" />
                      快速理解
                    </p>
                    <p className="mt-1 text-sm leading-6 text-emerald-900">{topic.plain_summary}</p>
                  </div>
                )}
                {topic.analogy && (
                  <div className="mt-2.5 rounded-lg border border-sky-100 bg-sky-50 p-3">
                    <p className="text-xs font-semibold text-sky-800">类比理解</p>
                    <p className="mt-1 text-sm leading-6 text-sky-900">{topic.analogy}</p>
                  </div>
                )}
                {topic.flow_steps && topic.flow_steps.length > 0 && (
                  <div className="mt-2.5 rounded-lg border border-zinc-200 bg-zinc-50 p-3">
                    <p className="flex items-center gap-1.5 text-xs font-semibold text-zinc-700">
                      <GitBranch className="size-3.5" aria-hidden="true" />
                      流程 / 关系
                    </p>
                    <ol className="mt-2 space-y-1.5">
                      {topic.flow_steps.map((step, index) => (
                        <li key={step} className="flex items-start gap-2">
                          <span className="mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full bg-white text-[10px] font-semibold text-zinc-700 ring-1 ring-zinc-300">
                            {index + 1}
                          </span>
                          <span className="text-sm leading-6 text-zinc-700">{step}</span>
                        </li>
                      ))}
                    </ol>
                  </div>
                )}
                {topic.study_points.length > 0 && (
                  <div className="mt-2.5">
                    <p className="text-xs font-semibold text-zinc-700">要补的内容</p>
                    <ul className="mt-1 space-y-1">
                      {topic.study_points.map((point) => (
                        <li key={point} className="text-sm leading-6 text-zinc-600">
                          · {point}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {topic.source_refs && topic.source_refs.length > 0 && (
                  <details className="mt-2.5 rounded-lg border border-zinc-200 bg-zinc-50 p-3">
                    <summary className="cursor-pointer text-xs font-semibold text-zinc-700">
                      {topic.source_status === 'missing' ? '当前原文片段（建议继续补齐）' : '原文依据（点开核对）'}
                    </summary>
                    <ul className="mt-2 space-y-2.5">
                      {topic.source_refs.map((ref, index) => (
                        <li key={`${ref.title}-${index}`} className="min-w-0">
                          <p className="truncate text-xs font-medium text-zinc-600">
                            《{ref.title}》
                          </p>
                          <blockquote className="mt-1 border-l-2 border-zinc-300 pl-2.5 text-sm leading-6 text-zinc-700">
                            {ref.text}
                          </blockquote>
                          {ref.url && (
                            <a
                              href={ref.url}
                              target="_blank"
                              rel="noreferrer"
                              className="mt-1 inline-flex items-center gap-1 text-xs font-medium text-emerald-700 hover:text-emerald-800"
                            >
                              打开原文
                              <ArrowRight className="size-3" aria-hidden="true" />
                            </a>
                          )}
                        </li>
                      ))}
                    </ul>
                  </details>
                )}
                {topic.source_status === 'missing' && (
                  <div className="mt-2.5 rounded-lg border border-amber-200 bg-amber-50 p-3">
                    <p className="text-xs font-semibold text-amber-800">原文档缺口</p>
                    <p className="mt-1 text-sm leading-6 text-amber-900">
                      {topic.source_note ||
                        '当前知识库片段还不足以解释这个错因，建议把相关概念、判断标准和例子补充到原文档后再练一次。'}
                    </p>
                  </div>
                )}
                {topic.next_actions.length > 0 && (
                  <div className="mt-2.5">
                    <p className="text-xs font-semibold text-zinc-700">怎么练</p>
                    <ul className="mt-1 space-y-1">
                      {topic.next_actions.map((action) => (
                        <li key={action} className="text-sm leading-6 text-emerald-800">
                          · {action}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}

      {review.study_plan.length > 0 && (
        <section className="tool-card">
          <h3 className="text-base font-semibold text-zinc-900">复习步骤</h3>
          <ol className="mt-3 space-y-2.5">
            {review.study_plan.map((rawStep, index) => {
              const step = typeof rawStep === 'string' ? { action: rawStep } : rawStep
              const refs = step.source_refs ?? []
              const isMissing = step.source_status === 'missing'
              return (
                <li
                  key={`${index}-${step.action}`}
                  className="flex gap-3 border-t border-zinc-100 pt-2.5 first:border-0 first:pt-0"
                >
                  <span className="flex size-6 shrink-0 items-center justify-center rounded-lg bg-emerald-600 text-xs font-semibold text-white">
                    {index + 1}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      {step.topic && (
                        <span className="rounded-md bg-zinc-100 px-2 py-0.5 text-xs font-medium text-zinc-700">
                          {step.topic}
                        </span>
                      )}
                      {refs.length > 0 && (
                        <span
                          className={`rounded-md px-2 py-0.5 text-xs font-medium ${
                            isMissing
                              ? 'bg-amber-100 text-amber-800'
                              : 'bg-emerald-50 text-emerald-700'
                          }`}
                        >
                          {isMissing ? '原文待补充' : '原文依据'}
                        </span>
                      )}
                    </div>
                    <p className="mt-1.5 text-sm leading-6 text-zinc-700">{step.action}</p>
                    {refs.length > 0 && (
                      <div className="mt-2 flex flex-wrap gap-1.5">
                        {refs.map((ref, refIndex) => {
                          const label = `《${ref.title}》`
                          return ref.url ? (
                            <a
                              key={`${ref.title}-${refIndex}`}
                              href={ref.url}
                              target="_blank"
                              rel="noreferrer"
                              className="inline-flex min-w-0 items-center gap-1 rounded-md bg-zinc-100 px-2 py-0.5 text-xs font-medium text-zinc-700 transition hover:text-emerald-700"
                            >
                              <span className="truncate">{label}</span>
                              <ArrowRight className="size-3 shrink-0" aria-hidden="true" />
                            </a>
                          ) : (
                            <span
                              key={`${ref.title}-${refIndex}`}
                              className="inline-flex min-w-0 items-center rounded-md bg-zinc-100 px-2 py-0.5 text-xs font-medium text-zinc-700"
                            >
                              <span className="truncate">{label}</span>
                            </span>
                          )
                        })}
                      </div>
                    )}
                    {isMissing && step.source_note && (
                      <p className="mt-1.5 text-xs leading-5 text-amber-800">{step.source_note}</p>
                    )}
                  </div>
                </li>
              )
            })}
          </ol>
          {review.encouragement && (
            <p className="mt-4 border-t border-zinc-100 pt-3 text-sm text-zinc-600">
              {review.encouragement}
            </p>
          )}
        </section>
      )}

      <section className="tool-card">
        <h3 className="text-base font-semibold text-zinc-900">逐题解析</h3>
        <ol className="mt-4 space-y-5">
          {attempt.questions.map((item, index) => (
            <li
              key={item.question_id}
              className="border-t border-zinc-100 pt-5 first:border-0 first:pt-0"
            >
              <div className="flex flex-wrap items-center gap-2">
                {item.is_correct ? (
                  <CheckCircle2 className="size-4 text-emerald-600" aria-hidden="true" />
                ) : (
                  <XCircle className="size-4 text-red-600" aria-hidden="true" />
                )}
                <span className="rounded-lg bg-zinc-100 px-2 py-0.5 text-xs font-medium text-zinc-700">
                  {TYPE_LABEL[item.type] ?? item.type}
                </span>
                {item.topic && <span className="text-xs text-zinc-500">{item.topic}</span>}
              </div>

              <p className="mt-2 text-sm font-medium leading-6 text-zinc-900">
                {index + 1}. {item.stem}
              </p>

              {!item.is_correct && (
                <p className="mt-1.5 text-xs leading-5 text-zinc-600">
                  你的答案：{item.user_answer.join('、') || '未作答'} ·
                  正确答案：{item.correct_answer.join('、')}
                </p>
              )}

              <ul className="mt-2.5 space-y-1.5">
                {item.options.map((option) => {
                  const isCorrect = item.correct_answer.includes(option.key)
                  const isPicked = item.user_answer.includes(option.key)
                  const optionStatus = isCorrect
                    ? isPicked
                      ? '你的选择 · 正确'
                      : '漏选'
                    : isPicked
                      ? '错误'
                      : ''
                  return (
                    <li
                      key={option.key}
                      className={`flex items-start gap-2.5 rounded-lg border px-3 py-2 text-sm leading-6 ${
                        isCorrect
                          ? 'border-emerald-300 bg-emerald-50 text-zinc-900'
                          : isPicked
                            ? 'border-red-300 bg-red-50 text-zinc-900'
                            : 'border-zinc-200 bg-white text-zinc-600'
                      }`}
                    >
                      <span className="mt-0.5 flex size-5 shrink-0 items-center justify-center rounded bg-white text-xs font-semibold text-zinc-600 ring-1 ring-zinc-200">
                        {option.key}
                      </span>
                      <span className="min-w-0 flex-1">{option.text}</span>
                      {isCorrect && (
                        <span className="shrink-0 text-xs font-medium text-emerald-700">
                          {optionStatus}
                        </span>
                      )}
                      {!isCorrect && isPicked && (
                        <span className="shrink-0 text-xs font-medium text-red-700">
                          {optionStatus}
                        </span>
                      )}
                    </li>
                  )
                })}
              </ul>

              {!item.is_correct && item.user_answer.length === 0 && (
                <p className="mt-2 text-xs text-red-700">这道题未作答。</p>
              )}

              {item.explanation && (
                <div className="mt-2.5 rounded-lg bg-zinc-50 px-3 py-2.5">
                  <p className="text-xs font-semibold text-zinc-700">解析</p>
                  <p className="mt-1 text-sm leading-6 text-zinc-600">{item.explanation}</p>
                  {item.source_title && (
                    <p className="mt-1.5 text-xs text-zinc-500">来源：{item.source_title}</p>
                  )}
                </div>
              )}
            </li>
          ))}
        </ol>
      </section>
    </div>
  )
}
