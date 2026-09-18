import {
  BookMarked,
  ChevronDown,
  ChevronRight,
  Trash2,
} from 'lucide-react'
import { useState } from 'react'
import { deleteMistake } from '../api'
import type { MistakeItem, QuizProgress, TopicProgress } from '../types'

interface MistakeBookProps {
  mistakes: MistakeItem[]
  progress: QuizProgress | null
  onMistakesChange: (mistakes: MistakeItem[]) => void
}

type TopicLevelFilter = 'all' | 'beginner' | 'developing' | 'proficient' | 'mastered'

const LEVEL_LABEL: Record<string, string> = {
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

export function MistakeBook({
  mistakes,
  progress,
  onMistakesChange,
}: MistakeBookProps) {
  const [expanded, setExpanded] = useState('')
  const [showAllTopics, setShowAllTopics] = useState(false)
  const [topicLevelFilter, setTopicLevelFilter] = useState<TopicLevelFilter>('all')
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
  const topicCounts = topics.reduce<Record<string, number>>((counts, topic) => {
    counts[topic.level] = (counts[topic.level] ?? 0) + 1
    return counts
  }, {})
  const filteredTopics = topicLevelFilter === 'all'
    ? topics
    : topics.filter((topic) => topic.level === topicLevelFilter)
  const visibleTopics = showAllTopics ? filteredTopics : filteredTopics.slice(0, 8)
  const focusActionByTopic = new Map(
    (progress?.focus_topics ?? []).map((topic) => [topic.topic, topic.recommended_action]),
  )
  const mistakeTopics = new Set(mistakes.map((item) => item.topic))

  const getTopicHint = (topic: TopicProgress) => {
    const focusedAction = focusActionByTopic.get(topic.topic)
    if (focusedAction) return focusedAction
    if (mistakeTopics.has(topic.topic)) {
      return '错题本中待巩固，先看解析，再换题型重做。'
    }
    if (topic.total < 4) {
      return `样本还少，再练 ${4 - topic.total} 道后判断是否稳定。`
    }
    if (topic.accuracy < 0.6) return '正确率偏低，先回读资料和错题解析。'
    const missingTypes = (
      ['single', 'multiple', 'judge'] as const
    ).filter((type) => (topic.by_type?.[type]?.correct ?? 0) === 0)
    if (!topic.cross_type_verified && missingTypes.length > 0) {
      const labels = missingTypes.slice(0, 2).map((type) => TYPE_LABEL[type]).join('、')
      return `还需要通过 ${labels} 换题型验证。`
    }
    if (topic.accuracy < 0.85) return '正确率未到 85%，继续用混合场景巩固。'
    if (topic.streak < 2) return '最近连续答对不足 2 次，再做一组确认稳定性。'
    if (topic.total >= 4 && topic.accuracy >= 0.85 && topic.cross_type_verified) {
      return '已跨题型掌握，后续间隔复习即可。'
    }
    return '继续保持混合题型练习。'
  }

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

      {progress?.module_stats && progress.module_stats.length > 0 && (
        <div className="mt-4">
          <p className="mb-2 text-xs font-semibold text-zinc-700">板块掌握度</p>
          <ul className="space-y-2">
            {progress.module_stats.slice(0, 5).map((module) => (
              <li key={module.module}>
                <div className="flex items-center justify-between gap-2 text-xs">
                  <span className="min-w-0 truncate text-zinc-700">{module.module}</span>
                  <span className="shrink-0 text-zinc-500">
                    {module.correct}/{module.total}
                  </span>
                </div>
                <div className="mt-1 h-1.5 overflow-hidden rounded-lg bg-zinc-200">
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
                <p className="mt-1 line-clamp-2 text-[11px] leading-4 text-zinc-500">
                  {module.recommendation}
                </p>
              </li>
            ))}
          </ul>
        </div>
      )}

      {topics.length > 0 && (
        <div className="mt-4">
          <div className="mb-2 flex items-center justify-between gap-2">
            <p className="text-xs font-semibold text-zinc-700">
              知识点掌握度（{filteredTopics.length}/{topics.length}）
            </p>
            <select
              aria-label="按掌握程度筛选知识点"
              className="h-8 max-w-[7.5rem] shrink-0 rounded-lg border border-zinc-300 bg-white px-2 text-xs text-zinc-700 outline-none transition focus:border-emerald-500"
              value={topicLevelFilter}
              onChange={(event) => {
                setTopicLevelFilter(event.target.value as TopicLevelFilter)
                setShowAllTopics(false)
              }}
            >
              <option value="all">全部（{topics.length}）</option>
              <option value="beginner">刚入门（{topicCounts.beginner ?? 0}）</option>
              <option value="developing">进步中（{topicCounts.developing ?? 0}）</option>
              <option value="proficient">较熟练（{topicCounts.proficient ?? 0}）</option>
              <option value="mastered">已掌握（{topicCounts.mastered ?? 0}）</option>
            </select>
          </div>
          <ul className="space-y-2">
            {visibleTopics.map((topic) => (
              <li key={topic.topic} className="rounded-lg bg-zinc-50 px-2.5 py-2">
                <div className="flex items-center justify-between gap-2 text-xs">
                  <span className="min-w-0 truncate font-medium text-zinc-800">{topic.topic}</span>
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
                <p className="mt-1 line-clamp-2 text-[11px] leading-4 text-zinc-500">
                  {getTopicHint(topic)}
                </p>
              </li>
            ))}
          </ul>
          {filteredTopics.length === 0 && (
            <p className="mt-2 text-xs leading-5 text-zinc-500">
              当前筛选等级下还没有知识点。
            </p>
          )}
          {visibleTopics.length < filteredTopics.length && (
            <button
              type="button"
              className="mt-2 inline-flex items-center gap-1 text-xs font-medium text-emerald-700 transition hover:text-emerald-800"
              onClick={() => setShowAllTopics((current) => !current)}
            >
              {showAllTopics ? <ChevronRight className="size-3.5" aria-hidden="true" /> : <ChevronDown className="size-3.5" aria-hidden="true" />}
              {showAllTopics ? '收起知识点' : `查看全部 ${filteredTopics.length} 个知识点`}
            </button>
          )}
        </div>
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
