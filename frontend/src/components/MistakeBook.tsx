import { ArrowRight, BookMarked } from 'lucide-react'
import type { QuizProgress, TopicProgress } from '../types'

interface MistakeBookProps {
  progress: QuizProgress | null
  reviewDueCount: number
  onOpenReview: () => void
}

const TYPE_LABEL: Record<string, string> = {
  single: '单选题',
  multiple: '多选题',
  judge: '判断题',
}

function getTopicHint(topic: TopicProgress): string {
  if (topic.total < 4) {
    return `样本还少，再练 ${4 - topic.total} 道后判断是否稳定。`
  }
  if (topic.accuracy < 0.6) return '正确率偏低，先回读资料和错题解析。'
  const missingTypes = (['single', 'multiple', 'judge'] as const).filter(
    (type) => (topic.by_type?.[type]?.correct ?? 0) === 0,
  )
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

export function MistakeBook({
  progress,
  reviewDueCount,
  onOpenReview,
}: MistakeBookProps) {
  const topics = (progress?.topics ?? [])
    .slice()
    .sort((left, right) => left.accuracy - right.accuracy)
    .slice(0, 5)
  const hasData = Boolean(progress && progress.answered_total > 0)

  return (
    <section className="tool-card">
      <h2 className="flex items-center gap-2 text-base font-semibold text-zinc-900">
        <BookMarked className="size-5 text-emerald-600" aria-hidden="true" />
        错题复盘入口
      </h2>

      <button
        type="button"
        className="mt-4 w-full rounded-lg border border-emerald-200 bg-emerald-50 p-4 text-left transition hover:border-emerald-300 hover:bg-emerald-100"
        onClick={onOpenReview}
      >
        <span className="flex items-center justify-between gap-3">
          <span>
            <span className="block text-sm font-semibold text-emerald-900">今日待复盘</span>
            <span className="mt-1 block text-xs leading-5 text-emerald-800">
              {hasData
                ? `${reviewDueCount} 道到期 · ${progress?.mistake_count ?? 0} 道在错题本`
                : '交卷后错题会自动进入间隔复习'}
            </span>
          </span>
          <ArrowRight className="size-4 shrink-0 text-emerald-700" aria-hidden="true" />
        </span>
      </button>

      {hasData && (
        <button
          type="button"
          className="secondary-btn mt-2 w-full py-2 text-xs"
          onClick={onOpenReview}
        >
          查看完整知识地图和全部错题
        </button>
      )}

      {progress?.module_stats && progress.module_stats.length > 0 && (
        <div className="mt-4">
          <p className="mb-2 text-xs font-semibold text-zinc-700">板块掌握度</p>
          <ul className="space-y-2">
            {progress.module_stats.slice(0, 3).map((module) => (
              <li key={module.module}>
                <div className="flex items-center justify-between gap-2 text-xs">
                  <span className="min-w-0 truncate text-zinc-700">{module.module}</span>
                  <span className="shrink-0 text-zinc-500">
                    {Math.round(module.accuracy * 100)}%
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
              </li>
            ))}
          </ul>
          <button
            type="button"
            className="mt-2 inline-flex items-center gap-1 text-xs font-medium text-emerald-700 transition hover:text-emerald-800"
            onClick={onOpenReview}
          >
            巩固薄弱板块
            <ArrowRight className="size-3" aria-hidden="true" />
          </button>
        </div>
      )}

      {topics.length > 0 && (
        <div className="mt-4 border-t border-zinc-100 pt-3">
          <p className="mb-2 text-xs font-semibold text-zinc-700">最弱知识点 TOP5</p>
          <ul className="space-y-2">
            {topics.map((topic) => (
              <li key={topic.topic} className="rounded-lg bg-zinc-50 px-2.5 py-2">
                <div className="flex items-center justify-between gap-2 text-xs">
                  <span className="min-w-0 truncate font-medium text-zinc-800">{topic.topic}</span>
                  <span className="shrink-0 text-zinc-500">
                    {topic.correct}/{topic.total}
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
                <button
                  type="button"
                  className="mt-1.5 inline-flex items-center gap-1 text-[11px] font-medium text-emerald-700 transition hover:text-emerald-800"
                  onClick={onOpenReview}
                >
                  再练这个知识点
                  <ArrowRight className="size-3" aria-hidden="true" />
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {!hasData && (
        <p className="mt-4 text-sm leading-6 text-zinc-500">
          完整知识点列表和错题记录在「错题复盘」页维护。
        </p>
      )}
    </section>
  )
}
