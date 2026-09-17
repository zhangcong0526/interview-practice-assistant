import { CheckCircle2, ChevronLeft, Loader2, Send } from 'lucide-react'
import { useMemo, useState } from 'react'
import { submitQuiz } from '../api'
import type { QuizAttempt, QuizPaper, QuizQuestion } from '../types'

interface QuizRunnerProps {
  paper: QuizPaper
  onGraded: (attempt: QuizAttempt) => void
  onCancel: () => void
}

const TYPE_LABEL: Record<string, string> = {
  single: '单选题',
  multiple: '多选题',
  judge: '判断题',
}

const DIFFICULTY_LABEL: Record<string, string> = {
  easy: '基础',
  medium: '中等',
  hard: '进阶',
}

export function QuizRunner({ paper, onGraded, onCancel }: QuizRunnerProps) {
  const [answers, setAnswers] = useState<Record<string, string[]>>({})
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  const answeredCount = useMemo(
    () => paper.questions.filter((q) => (answers[q.question_id] ?? []).length > 0).length,
    [answers, paper.questions],
  )

  const pick = (question: QuizQuestion, optionKey: string) => {
    setAnswers((current) => {
      const picked = current[question.question_id] ?? []
      if (question.type === 'multiple') {
        const next = picked.includes(optionKey)
          ? picked.filter((key) => key !== optionKey)
          : [...picked, optionKey].sort()
        return { ...current, [question.question_id]: next }
      }
      return { ...current, [question.question_id]: [optionKey] }
    })
  }

  const handleSubmit = async () => {
    if (submitting) return
    setSubmitting(true)
    setError('')
    try {
      onGraded(await submitQuiz(paper.paper_id, answers))
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : '交卷失败，请重试。')
      setSubmitting(false)
    }
  }

  const allAnswered = answeredCount === paper.questions.length

  return (
    <section className="tool-card">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-base font-semibold text-zinc-900">{paper.title}</h2>
          <p className="mt-1 text-xs text-zinc-500">
            共 {paper.questions.length} 题 · 已作答 {answeredCount} 题
            {paper.doc_titles.length > 0 && ` · 来源：${paper.doc_titles.join('、')}`}
          </p>
        </div>
        <button type="button" className="secondary-btn px-3 py-2 text-xs" onClick={onCancel}>
          <ChevronLeft className="size-3.5" aria-hidden="true" />
          返回组卷
        </button>
      </div>

      <div className="mt-3 h-1.5 w-full overflow-hidden rounded-lg bg-zinc-200">
        <div
          className="h-full rounded-lg bg-emerald-600 transition-all"
          style={{ width: `${(answeredCount / paper.questions.length) * 100}%` }}
        />
      </div>

      <ol className="mt-5 space-y-5">
        {paper.questions.map((question, index) => {
          const picked = answers[question.question_id] ?? []
          return (
            <li key={question.question_id} className="border-t border-zinc-100 pt-5 first:border-0 first:pt-0">
              <div className="flex flex-wrap items-center gap-2">
                <span className="rounded-lg bg-zinc-100 px-2 py-0.5 text-xs font-medium text-zinc-700">
                  {TYPE_LABEL[question.type] ?? question.type}
                </span>
                <span className="text-xs text-zinc-500">
                  {DIFFICULTY_LABEL[question.difficulty] ?? question.difficulty}
                </span>
                {question.topic && (
                  <span className="text-xs text-zinc-500">· {question.topic}</span>
                )}
              </div>
              <p className="mt-2 text-sm font-medium leading-6 text-zinc-900">
                {index + 1}. {question.stem}
              </p>
              <ul className="mt-3 space-y-2">
                {question.options.map((option) => {
                  const chosen = picked.includes(option.key)
                  return (
                    <li key={option.key}>
                      <button
                        type="button"
                        className={`flex w-full items-start gap-2.5 rounded-lg border px-3 py-2.5 text-left text-sm leading-6 transition ${
                          chosen
                            ? 'border-emerald-500 bg-emerald-50 text-zinc-900'
                            : 'border-zinc-200 bg-white text-zinc-700 hover:border-zinc-300'
                        }`}
                        onClick={() => pick(question, option.key)}
                        aria-pressed={chosen}
                      >
                        <span
                          className={`mt-0.5 flex size-5 shrink-0 items-center justify-center text-xs font-semibold ${
                            question.type === 'multiple' ? 'rounded' : 'rounded-full'
                          } ${
                            chosen
                              ? 'bg-emerald-600 text-white'
                              : 'bg-zinc-100 text-zinc-500'
                          }`}
                        >
                          {option.key}
                        </span>
                        <span>{option.text}</span>
                      </button>
                    </li>
                  )
                })}
              </ul>
            </li>
          )
        })}
      </ol>

      {error && <p className="mt-4 text-sm leading-5 text-red-600">{error}</p>}

      {!allAnswered && (
        <p className="mt-4 text-xs text-zinc-500">
          还有 {paper.questions.length - answeredCount} 题未作答，未作答的题目会判为错误。
        </p>
      )}

      <button
        type="button"
        className="primary-btn mt-4 w-full py-3"
        onClick={handleSubmit}
        disabled={submitting || answeredCount === 0}
      >
        {submitting ? (
          <Loader2 className="size-5 animate-spin" aria-hidden="true" />
        ) : allAnswered ? (
          <CheckCircle2 className="size-5" aria-hidden="true" />
        ) : (
          <Send className="size-5" aria-hidden="true" />
        )}
        {submitting ? '正在判卷并生成复习指引' : '提交答卷'}
      </button>
    </section>
  )
}
