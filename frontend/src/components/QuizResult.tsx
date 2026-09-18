import {
  AlertTriangle,
  ArrowRight,
  BookMarked,
  CheckCircle2,
  Loader2,
  RotateCcw,
  Sparkles,
  Target,
  XCircle,
} from 'lucide-react'
import { useState } from 'react'
import { generateMistakePaper, generateQuizPaper } from '../api'
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

export function QuizResult({
  attempt,
  cumulativeMistakeCount,
  onRetryMistakes,
  onBackToSetup,
}: QuizResultProps) {
  const [busy, setBusy] = useState(false)
  const [nextBusy, setNextBusy] = useState(false)
  const [error, setError] = useState('')
  const [mistakeScope, setMistakeScope] = useState<MistakeQuizScope>('current')
  const { review } = attempt
  const guide = review.learning_guide
  const wrongQuestions = attempt.questions.filter((item) => !item.is_correct)
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
        }),
      )
    } catch (retryError) {
      setError(retryError instanceof Error ? retryError.message : '错题重练组卷失败。')
      setBusy(false)
    }
  }

  const handleSuggestedPaper = async () => {
    if (!guide || nextBusy) return
    setNextBusy(true)
    setError('')
    try {
      onRetryMistakes(
        await generateQuizPaper({
          keywords: guide.next_paper.keywords,
          doc_ids: [],
          single: guide.next_paper.single,
          multiple: guide.next_paper.multiple,
          judge: guide.next_paper.judge,
          difficulty: 'mixed',
          focus_weak: true,
        }),
      )
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : '建议组卷失败。')
      setNextBusy(false)
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

            <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-3">
              {guide.type_stats.map((stat) => (
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
              <p className="text-xs font-semibold text-zinc-700">已跨题型掌握</p>
              {guide.mastered_topics.length > 0 ? (
                <div className="mt-1.5 flex flex-wrap gap-1.5">
                  {guide.mastered_topics.slice(0, 8).map((topic) => (
                    <span
                      key={topic.topic}
                      className="rounded-full bg-emerald-100 px-2.5 py-1 text-xs text-emerald-800"
                      title={`已通过：${topic.verified_types.map((type) => TYPE_LABEL[type] ?? type).join('、')}`}
                    >
                      {topic.topic}
                    </span>
                  ))}
                </div>
              ) : (
                <p className="mt-1 text-xs leading-5 text-zinc-500">
                  还没有知识点满足“累计 4 题、正确率 85% 以上、至少两种题型答对”的标准。
                </p>
              )}
            </div>

            <div className="mt-3">
              <p className="text-xs font-semibold text-zinc-700">下一步重点</p>
              {guide.focus_topics.length > 0 ? (
                <ul className="mt-1.5 space-y-2">
                  {guide.focus_topics.slice(0, 5).map((topic) => (
                    <li key={topic.topic} className="rounded-lg bg-white px-3 py-2 ring-1 ring-zinc-200">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <span className="text-xs font-semibold text-zinc-800">{topic.topic}</span>
                        <span className="text-xs text-zinc-500">
                          {topic.correct}/{topic.total} · 正确率 {Math.round(topic.accuracy * 100)}%
                        </span>
                      </div>
                      <p className="mt-1 text-xs leading-5 text-zinc-600">
                        {topic.reasons.join('；')}
                      </p>
                      <p className="mt-1 text-xs leading-5 text-emerald-800">
                        {topic.recommended_action}
                      </p>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="mt-1 text-xs leading-5 text-zinc-500">当前知识点掌握稳定，可以做一套混合卷保持手感。</p>
              )}
            </div>

            {guide.module_stats && guide.module_stats.length > 0 && (
              <div className="mt-3">
                <p className="text-xs font-semibold text-zinc-700">板块掌握度</p>
                <div className="mt-1.5 grid gap-2 sm:grid-cols-2">
                  {guide.module_stats.slice(0, 4).map((module) => (
                    <div key={module.module} className="rounded-lg bg-white px-3 py-2 ring-1 ring-zinc-200">
                      <div className="flex items-center justify-between gap-2">
                        <span className="truncate text-xs font-semibold text-zinc-800">
                          {module.module}
                        </span>
                        <span className="shrink-0 text-xs text-zinc-500">
                          {module.mastered_topic_count}/{module.topic_count} 点
                        </span>
                      </div>
                      <div className="mt-1.5 h-1.5 overflow-hidden rounded-lg bg-zinc-200">
                        <div
                          className={`h-full rounded-lg ${
                            module.accuracy >= 0.85
                              ? 'bg-emerald-600'
                              : module.accuracy >= 0.6
                                ? 'bg-amber-500'
                                : 'bg-red-500'
                          }`}
                          style={{ width: `${Math.max(4, module.accuracy * 100)}%` }}
                        />
                      </div>
                      <p className="mt-1 line-clamp-2 text-xs leading-5 text-zinc-500">
                        {module.recommendation}
                      </p>
                    </div>
                  ))}
                </div>
              </div>
            )}

            <div className="mt-3 rounded-lg bg-white px-3 py-2 ring-1 ring-zinc-200">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="text-xs font-semibold text-zinc-700">下一套建议</p>
                {guide.next_paper.keywords.length > 0 && (
                  <button
                    type="button"
                    className="inline-flex items-center gap-1 rounded-md bg-emerald-600 px-2.5 py-1 text-xs font-semibold text-white transition hover:bg-emerald-700 disabled:opacity-60"
                    onClick={handleSuggestedPaper}
                    disabled={nextBusy}
                  >
                    {nextBusy ? (
                      <Loader2 className="size-3.5 animate-spin" aria-hidden="true" />
                    ) : (
                      <Sparkles className="size-3.5" aria-hidden="true" />
                    )}
                    一键生成
                  </button>
                )}
              </div>
              <p className="mt-1 text-xs leading-5 text-zinc-600">{guide.next_paper.reason}</p>
              <p className="mt-1 text-xs text-zinc-500">
                建议题量：单选 {guide.next_paper.single} · 多选 {guide.next_paper.multiple} · 判断 {guide.next_paper.judge}
              </p>
              {guide.next_paper.keywords.length > 0 && (
                <div className="mt-1.5 flex flex-wrap gap-1.5">
                  {guide.next_paper.keywords.map((keyword) => (
                    <span key={keyword} className="rounded-full bg-zinc-100 px-2 py-0.5 text-xs text-zinc-700">
                      {keyword}
                    </span>
                  ))}
                </div>
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
        {error && <p className="mt-3 text-sm leading-5 text-red-600">{error}</p>}
      </section>

      {review.weak_topics.length > 0 && (
        <section className="tool-card">
          <h3 className="text-base font-semibold text-zinc-900">需要补的知识点</h3>
          <ul className="mt-4 space-y-4">
            {review.weak_topics.map((topic) => (
              <li key={topic.topic} className="border-t border-zinc-100 pt-4 first:border-0 first:pt-0">
                <p className="text-sm font-semibold text-zinc-900">{topic.topic}</p>
                {topic.diagnosis && (
                  <p className="mt-1.5 text-sm leading-6 text-zinc-600">{topic.diagnosis}</p>
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
            {review.study_plan.map((step, index) => (
              <li key={step} className="flex gap-3 text-sm leading-6 text-zinc-700">
                <span className="flex size-6 shrink-0 items-center justify-center rounded-lg bg-emerald-600 text-xs font-semibold text-white">
                  {index + 1}
                </span>
                <span>{step}</span>
              </li>
            ))}
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

              <ul className="mt-2.5 space-y-1.5">
                {item.options.map((option) => {
                  const isCorrect = item.correct_answer.includes(option.key)
                  const isPicked = item.user_answer.includes(option.key)
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
                        <span className="shrink-0 text-xs font-medium text-emerald-700">正确答案</span>
                      )}
                      {isPicked && !isCorrect && (
                        <span className="shrink-0 text-xs font-medium text-red-700">你的选择</span>
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
