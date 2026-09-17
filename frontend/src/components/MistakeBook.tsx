import {
  BookMarked,
  ChevronDown,
  ChevronRight,
  Trash2,
} from 'lucide-react'
import { useState } from 'react'
import { deleteMistake } from '../api'
import type { MistakeItem, QuizProgress } from '../types'

interface MistakeBookProps {
  mistakes: MistakeItem[]
  progress: QuizProgress | null
  onMistakesChange: (mistakes: MistakeItem[]) => void
}

const LEVEL_LABEL: Record<string, string> = {
  beginner: '刚入门',
  developing: '进步中',
  proficient: '较熟练',
  mastered: '已掌握',
}

export function MistakeBook({
  mistakes,
  progress,
  onMistakesChange,
}: MistakeBookProps) {
  const [expanded, setExpanded] = useState('')
  const [error, setError] = useState('')

  const handleDelete = async (key: string) => {
    try {
      await deleteMistake(key)
      onMistakesChange(mistakes.filter((item) => item.key !== key))
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : '移除错题失败。')
    }
  }

  const topics = progress?.topics ?? []

  return (
    <section className="tool-card">
      <h2 className="flex items-center gap-2 text-base font-semibold text-zinc-900">
        <BookMarked className="size-5 text-emerald-600" aria-hidden="true" />
        错题本与掌握度
      </h2>

      {progress && progress.answered_total > 0 ? (
        <dl className="mt-3 grid grid-cols-3 gap-2 text-center">
          <div className="rounded-lg bg-zinc-50 px-2 py-2.5">
            <dt className="text-xs text-zinc-500">累计答题</dt>
            <dd className="mt-0.5 text-lg font-semibold text-zinc-900">
              {progress.answered_total}
            </dd>
          </div>
          <div className="rounded-lg bg-zinc-50 px-2 py-2.5">
            <dt className="text-xs text-zinc-500">总正确率</dt>
            <dd className="mt-0.5 text-lg font-semibold text-zinc-900">
              {Math.round(progress.overall_accuracy * 100)}%
            </dd>
          </div>
          <div className="rounded-lg bg-zinc-50 px-2 py-2.5">
            <dt className="text-xs text-zinc-500">待巩固</dt>
            <dd className="mt-0.5 text-lg font-semibold text-zinc-900">
              {progress.mistake_count}
            </dd>
          </div>
        </dl>
      ) : (
        <p className="mt-3 text-sm leading-6 text-zinc-500">
          还没有答题记录。生成一份试卷交卷后，这里会记录每个知识点的掌握情况。
        </p>
      )}

      {topics.length > 0 && (
        <ul className="mt-4 space-y-2">
          {topics.slice(0, 8).map((topic) => (
            <li key={topic.topic}>
              <div className="flex items-center justify-between gap-2 text-xs">
                <span className="min-w-0 truncate text-zinc-700">{topic.topic}</span>
                <span className="shrink-0 text-zinc-500">
                  {topic.correct}/{topic.total} · {LEVEL_LABEL[topic.level] ?? topic.level}
                </span>
              </div>
              <div className="mt-1 h-1.5 w-full overflow-hidden rounded-lg bg-zinc-200">
                <div
                  className={`h-full rounded-lg ${
                    topic.accuracy >= 0.85
                      ? 'bg-emerald-600'
                      : topic.accuracy >= 0.6
                        ? 'bg-amber-500'
                        : 'bg-red-500'
                  }`}
                  style={{ width: `${Math.max(4, topic.accuracy * 100)}%` }}
                />
              </div>
            </li>
          ))}
        </ul>
      )}

      {error && <p className="mt-3 text-sm leading-5 text-red-600">{error}</p>}

      {mistakes.length > 0 && (
        <div className="mt-4 border-t border-zinc-100 pt-3">
          <p className="mb-2 text-xs font-semibold text-zinc-700">
            错题记录（{mistakes.length}）
          </p>
          <ul className="space-y-2">
            {mistakes.slice(0, 12).map((item) => {
              const open = expanded === item.key
              return (
                <li key={item.key} className="rounded-lg border border-zinc-200 bg-zinc-50">
                  <div className="flex items-start gap-1">
                    <button
                      type="button"
                      className="flex min-w-0 flex-1 items-start gap-2 px-3 py-2 text-left"
                      onClick={() => setExpanded(open ? '' : item.key)}
                      aria-expanded={open}
                    >
                      {open ? (
                        <ChevronDown className="mt-0.5 size-3.5 shrink-0 text-zinc-400" aria-hidden="true" />
                      ) : (
                        <ChevronRight className="mt-0.5 size-3.5 shrink-0 text-zinc-400" aria-hidden="true" />
                      )}
                      <span className="min-w-0">
                        <span className="block truncate text-xs font-medium text-zinc-800">
                          {item.topic}
                        </span>
                        <span className="mt-0.5 block truncate text-xs text-zinc-500">
                          错 {item.wrong_count} 次 · {item.stem}
                        </span>
                      </span>
                    </button>
                    <button
                      type="button"
                      className="shrink-0 rounded-lg p-2 text-zinc-400 transition hover:bg-red-50 hover:text-red-600"
                      onClick={() => handleDelete(item.key)}
                      aria-label="移出错题本"
                      title="移出错题本"
                    >
                      <Trash2 className="size-3.5" aria-hidden="true" />
                    </button>
                  </div>

                  {open && (
                    <div className="space-y-1.5 border-t border-zinc-200 px-3 py-2.5 text-xs leading-5">
                      <p className="text-zinc-700">{item.stem}</p>
                      <p className="text-emerald-800">
                        正确答案：{item.correct_answer_text}
                      </p>
                      {item.user_answer_text && (
                        <p className="text-red-700">你的答案：{item.user_answer_text}</p>
                      )}
                      {item.explanation && (
                        <p className="text-zinc-600">{item.explanation}</p>
                      )}
                    </div>
                  )}
                </li>
              )
            })}
          </ul>
          <p className="mt-2 text-xs text-zinc-500">
            同一道题连续答对两次会自动移出错题本。
          </p>
        </div>
      )}
    </section>
  )
}
