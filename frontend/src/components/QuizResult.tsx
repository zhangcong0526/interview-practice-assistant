import {
  AlertTriangle,
  ArrowRight,
  BookMarked,
  CheckCircle2,
  Loader2,
  RotateCcw,
  Target,
  XCircle,
} from 'lucide-react'
import { useState } from 'react'
import { generateMistakePaper } from '../api'
import type { MasteryLevel, QuizAttempt, QuizPaper } from '../types'

interface QuizResultProps {
  attempt: QuizAttempt
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
  onRetryMistakes,
  onBackToSetup,
}: QuizResultProps) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const { review } = attempt
  const wrongQuestions = attempt.questions.filter((item) => !item.is_correct)

  const handleRetry = async () => {
    if (busy) return
    setBusy(true)
    setError('')
    try {
      onRetryMistakes(await generateMistakePaper(8, 'mixed'))
    } catch (retryError) {
      setError(retryError instanceof Error ? retryError.message : '错题重练组卷失败。')
      setBusy(false)
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

        {review.review_error && (
          <p className="mt-3 flex items-start gap-2 text-xs leading-5 text-amber-700">
            <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
            <span>{review.review_error}（得分为本地判分，不受影响）</span>
          </p>
        )}

        <div className="mt-4 flex flex-wrap gap-3 border-t border-zinc-100 pt-4">
          {wrongQuestions.length > 0 && (
            <button type="button" className="primary-btn" onClick={handleRetry} disabled={busy}>
              {busy ? (
                <Loader2 className="size-4 animate-spin" aria-hidden="true" />
              ) : (
                <RotateCcw className="size-4" aria-hidden="true" />
              )}
              针对错题再考一次
            </button>
          )}
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
