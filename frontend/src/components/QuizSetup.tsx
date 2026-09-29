import {
  AlertTriangle,
  Check,
  FilePlus2,
  Loader2,
  RefreshCw,
  RotateCcw,
  Search,
  Tags,
} from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import {
  extractQuizTopics,
  generateMistakePaper,
  generateQuizPaper,
  listMistakes,
} from '../api'
import type {
  KnowledgeDocument,
  MistakeItem,
  MistakeQuizScope,
  MistakeTimeRange,
  QuizPaper,
  QuizProgress,
  QuizTopic,
} from '../types'

interface QuizSetupProps {
  documents: KnowledgeDocument[]
  mistakeCount: number
  progress: QuizProgress | null
  onPaperReady: (paper: QuizPaper) => void
}

const DIFFICULTIES: { value: string; label: string }[] = [
  { value: 'easy', label: '基础' },
  { value: 'mixed', label: '混合' },
  { value: 'hard', label: '进阶' },
]

const MISTAKE_TIME_RANGES: { value: MistakeTimeRange; label: string }[] = [
  { value: 'last', label: '上次错题' },
  { value: '1d', label: '近1天' },
  { value: '7d', label: '近7天' },
  { value: 'all', label: '累计' },
]

// 单次组卷上限与后端保持一致（单份最多 30 道）。
// 题量再大，LLM 一次性命题容易超时，也不利于一次练完，超量应拆成多套。
const MAX_TOTAL_QUESTIONS = 30

export function QuizSetup({
  documents,
  mistakeCount,
  progress,
  onPaperReady,
}: QuizSetupProps) {
  const [topics, setTopics] = useState<QuizTopic[]>([])
  const [selected, setSelected] = useState<string[]>([])
  const [filter, setFilter] = useState('')
  const [docIds, setDocIds] = useState<string[]>([])
  const [single, setSingle] = useState(10)
  const [multiple, setMultiple] = useState(6)
  const [judge, setJudge] = useState(4)
  const [difficulty, setDifficulty] = useState('mixed')
  const [loadingTopics, setLoadingTopics] = useState(false)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [mistakePanelOpen, setMistakePanelOpen] = useState(false)
  const [mistakes, setMistakes] = useState<MistakeItem[]>([])
  const [mistakesLoaded, setMistakesLoaded] = useState(false)
  const [loadingMistakes, setLoadingMistakes] = useState(false)
  const [mistakeTimeRange, setMistakeTimeRange] = useState<MistakeTimeRange>('all')
  const [selectedMistakeTopics, setSelectedMistakeTopics] = useState<string[]>([])
  const [mistakeTotal, setMistakeTotal] = useState(20)
  const topicsRequestRef = useRef(0)
  const mistakesRequestRef = useRef(0)
  const latestAttempt = progress?.recent_attempts?.[0] ?? null
  const currentMistakeCount = latestAttempt
    ? Math.max(0, latestAttempt.total - latestAttempt.correct_count)
    : 0

  const loadTopics = async (refresh = false) => {
    if (documents.length === 0) return
    // 提炼关键词要走一次 LLM，耗时不稳定。切换资料时旧请求可能还没回来，
    // 若不加序号，慢的旧响应会在新响应之后落地，把关键词覆盖回上一个资料。
    const request = ++topicsRequestRef.current
    setLoadingTopics(true)
    setError('')
    try {
      const next = await extractQuizTopics(docIds, refresh)
      if (request !== topicsRequestRef.current) return
      setTopics(next)
    } catch (loadError) {
      if (request !== topicsRequestRef.current) return
      setError(loadError instanceof Error ? loadError.message : '关键词提炼失败。')
    } finally {
      if (request === topicsRequestRef.current) {
        setLoadingTopics(false)
      }
    }
  }

  useEffect(() => {
    void loadTopics(false)
    // 文档集合变化时重新提炼关键词。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [documents.length, docIds.join(',')])

  const grouped = useMemo(() => {
    const keyword = filter.trim().toLowerCase()
    const map = new Map<string, QuizTopic[]>()
    for (const topic of topics) {
      if (
        keyword &&
        !topic.keyword.toLowerCase().includes(keyword) &&
        !topic.category.toLowerCase().includes(keyword) &&
        !topic.aliases.some((alias) => alias.toLowerCase().includes(keyword))
      ) {
        continue
      }
      const list = map.get(topic.category) ?? []
      list.push(topic)
      map.set(topic.category, list)
    }
    return [...map.entries()]
  }, [topics, filter])

  const toggleTopic = (keyword: string) => {
    setSelected((current) =>
      current.includes(keyword)
        ? current.filter((item) => item !== keyword)
        : [...current, keyword],
    )
  }

  const toggleMistakeTopic = (topic: string) => {
    setSelectedMistakeTopics((current) =>
      current.includes(topic)
        ? current.filter((item) => item !== topic)
        : [...current, topic],
    )
  }

  const toggleCategory = (category: string, items: QuizTopic[]) => {
    const keys = items.map((item) => item.keyword)
    const allChosen = keys.every((key) => selected.includes(key))
    setSelected((current) =>
      allChosen
        ? current.filter((item) => !keys.includes(item))
        : [...new Set([...current, ...keys])],
    )
    void category
  }

  const toggleDoc = (docId: string) => {
    setDocIds((current) =>
      current.includes(docId)
        ? current.filter((item) => item !== docId)
        : [...current, docId],
    )
  }

  const loadMistakes = async () => {
    const request = ++mistakesRequestRef.current
    setLoadingMistakes(true)
    setError('')
    try {
      const next = await listMistakes()
      if (request !== mistakesRequestRef.current) return
      setMistakes(next)
      setMistakesLoaded(true)
    } catch (loadError) {
      if (request !== mistakesRequestRef.current) return
      setError(loadError instanceof Error ? loadError.message : '错题本加载失败。')
    } finally {
      if (request === mistakesRequestRef.current) {
        setLoadingMistakes(false)
      }
    }
  }

  useEffect(() => {
    if (!mistakePanelOpen) return
    void loadMistakes()
  }, [mistakePanelOpen])

  const filteredMistakes = useMemo(() => {
    if (!mistakesLoaded) return []
    if (mistakeTimeRange === 'last') {
      const paperId = latestAttempt?.paper_id ?? ''
      return mistakes.filter((item) => item.paper_id === paperId)
    }
    if (mistakeTimeRange === '1d' || mistakeTimeRange === '7d') {
      const windowSeconds = mistakeTimeRange === '1d' ? 86_400 : 7 * 86_400
      const threshold = Math.floor(Date.now() / 1000) - windowSeconds
      return mistakes.filter(
        (item) => Math.max(item.last_seen_at || 0, item.created_at || 0) >= threshold,
      )
    }
    return mistakes
  }, [latestAttempt?.paper_id, mistakeTimeRange, mistakes, mistakesLoaded])

  const mistakeTopicOptions = useMemo(() => {
    const counts = new Map<string, number>()
    for (const item of filteredMistakes) {
      const topic = item.topic || '未分类'
      counts.set(topic, (counts.get(topic) || 0) + 1)
    }
    return [...counts.entries()]
      .map(([topic, count]) => ({ topic, count }))
      .sort((left, right) => right.count - left.count || left.topic.localeCompare(right.topic))
  }, [filteredMistakes])

  const mistakeTopicKey = mistakeTopicOptions.map((item) => item.topic).join('\u0000')

  useEffect(() => {
    if (!mistakesLoaded) return
    const available = new Set(mistakeTopicKey ? mistakeTopicKey.split('\u0000') : [])
    setSelectedMistakeTopics((current) => current.filter((topic) => available.has(topic)))
  }, [mistakeTopicKey, mistakesLoaded])

  const total = single + multiple + judge
  const overLimit = total > MAX_TOTAL_QUESTIONS

  const handleGenerate = async () => {
    if (selected.length === 0 || busy) return
    if (total < 1) {
      setError('至少要出一道题。')
      return
    }
    if (overLimit) {
      setError(`一次最多出 ${MAX_TOTAL_QUESTIONS} 道题，请调低某个题型的数量。`)
      return
    }
    setBusy('generate')
    setError('')
    try {
      const paper = await generateQuizPaper({
        keywords: selected,
        doc_ids: docIds,
        single,
        multiple,
        judge,
        difficulty,
        focus_weak: true,
      })
      onPaperReady(paper)
    } catch (generateError) {
      setError(generateError instanceof Error ? generateError.message : '组卷失败。')
    } finally {
      setBusy('')
    }
  }

  const handleMistakeQuiz = async (scope: MistakeQuizScope) => {
    if (busy || scope !== 'current' || currentMistakeCount === 0) return
    setBusy('mistake-current')
    setError('')
    try {
      onPaperReady(
        await generateMistakePaper({
          scope,
          attempt_id: scope === 'current' ? latestAttempt?.attempt_id ?? '' : '',
          limit: 8,
          difficulty,
        }),
      )
    } catch (generateError) {
      setError(generateError instanceof Error ? generateError.message : '错题重练组卷失败。')
    } finally {
      setBusy('')
    }
  }

  const handleCustomMistakeQuiz = async () => {
    if (busy || filteredMistakes.length === 0) return
    if (mistakeTotal < 1 || mistakeTotal > MAX_TOTAL_QUESTIONS) {
      setError(`错题重练题量需在 1 到 ${MAX_TOTAL_QUESTIONS} 道之间。`)
      return
    }
    setBusy('mistake-all')
    setError('')
    try {
      onPaperReady(
        await generateMistakePaper({
          scope: 'all',
          time_range: mistakeTimeRange,
          topics: selectedMistakeTopics,
          total: mistakeTotal,
          difficulty,
        }),
      )
    } catch (generateError) {
      setError(generateError instanceof Error ? generateError.message : '错题重练组卷失败。')
    } finally {
      setBusy('')
    }
  }

  if (documents.length === 0) {
    return (
      <section className="tool-card">
        <h2 className="flex items-center gap-2 text-base font-semibold text-zinc-900">
          <Tags className="size-5 text-emerald-600" aria-hidden="true" />
          在线考题
        </h2>
        <p className="mt-3 flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm leading-6 text-amber-800">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          <span>
            还没有知识库文档。先在右侧「测试知识库」导入你的面试错题本或面试纪要，再回到这里生成考题。
          </span>
        </p>
      </section>
    )
  }

  return (
    <section className="tool-card">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="flex items-center gap-2 text-base font-semibold text-zinc-900">
          <Tags className="size-5 text-emerald-600" aria-hidden="true" />
          选择知识点组卷
        </h2>
        <div className="flex flex-wrap items-center gap-2">
          {currentMistakeCount > 0 && (
            <button
              type="button"
              className="secondary-btn px-3 py-2 text-xs"
              onClick={() => handleMistakeQuiz('current')}
              disabled={Boolean(busy)}
              title="按本卷错题知识点生成变形题"
            >
              {busy === 'mistake-current' ? (
                <Loader2 className="size-3.5 animate-spin" aria-hidden="true" />
              ) : (
                <RotateCcw className="size-3.5" aria-hidden="true" />
              )}
              {busy === 'mistake-current'
                ? '正在生成变形题'
                : `本次错题变形练（${currentMistakeCount}）`}
            </button>
          )}
          {mistakeCount > 0 && (
            <button
              type="button"
              className="secondary-btn px-3 py-2 text-xs"
              onClick={() => setMistakePanelOpen((current) => !current)}
              disabled={Boolean(busy)}
              aria-expanded={mistakePanelOpen}
              title="选择时间范围、知识点和题量后重练错题"
            >
              {busy === 'mistake-all' ? (
                <Loader2 className="size-3.5 animate-spin" aria-hidden="true" />
              ) : (
                <RotateCcw className="size-3.5" aria-hidden="true" />
              )}
              {busy === 'mistake-all' ? '正在生成重练卷' : '自定义错题重练'}
            </button>
          )}
          <button
            type="button"
            className="secondary-btn px-3 py-2 text-xs"
            onClick={() => void loadTopics(true)}
            disabled={loadingTopics || Boolean(busy)}
            title="重新从知识库提炼关键词"
          >
            {loadingTopics ? (
              <Loader2 className="size-3.5 animate-spin" aria-hidden="true" />
            ) : (
              <RefreshCw className="size-3.5" aria-hidden="true" />
            )}
            重新提炼
          </button>
        </div>
      </div>

      {mistakePanelOpen && (
        <div className="mt-4 rounded-lg border border-zinc-200 bg-zinc-50 p-3.5">
          <div>
            <span className="field-label">时间范围</span>
            <div className="flex flex-wrap gap-1.5">
              {MISTAKE_TIME_RANGES.map((item) => (
                <button
                  key={item.value}
                  type="button"
                  className={`rounded-lg border px-2.5 py-1.5 text-xs transition ${
                    mistakeTimeRange === item.value
                      ? 'border-emerald-500 bg-emerald-600 text-white'
                      : 'border-zinc-300 bg-white text-zinc-700 hover:border-emerald-400'
                  }`}
                  onClick={() => setMistakeTimeRange(item.value)}
                  aria-pressed={mistakeTimeRange === item.value}
                >
                  {item.label}
                </button>
              ))}
            </div>
          </div>

          <div className="mt-3">
            <span className="field-label">知识点标签</span>
            {loadingMistakes && !mistakesLoaded ? (
              <p className="flex items-center gap-2 text-sm text-zinc-500">
                <Loader2 className="size-4 animate-spin" aria-hidden="true" />
                正在读取错题本
              </p>
            ) : mistakeTopicOptions.length === 0 ? (
              <p className="text-sm text-zinc-500">当前时间范围内没有错题。</p>
            ) : (
              <>
                <ul className="flex max-h-36 flex-wrap gap-1.5 overflow-y-auto rounded-lg border border-zinc-200 bg-white p-2">
                  {mistakeTopicOptions.map((option) => {
                    const chosen = selectedMistakeTopics.includes(option.topic)
                    return (
                      <li key={option.topic}>
                        <button
                          type="button"
                          className={`inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs transition ${
                            chosen
                              ? 'border-emerald-500 bg-emerald-600 text-white'
                              : 'border-zinc-300 bg-white text-zinc-700 hover:border-emerald-400'
                          }`}
                          onClick={() => toggleMistakeTopic(option.topic)}
                          aria-pressed={chosen}
                        >
                          {chosen && <Check className="size-3.5" aria-hidden="true" />}
                          {option.topic}（{option.count}）
                        </button>
                      </li>
                    )
                  })}
                </ul>
                <p className="mt-1.5 text-xs text-zinc-500">
                  不选知识点时使用当前时间范围内的全部错题。
                </p>
              </>
            )}
          </div>

          <div className="mt-3 flex flex-wrap items-end justify-between gap-3">
            <div className="w-28">
              <label htmlFor="mistake-total" className="field-label">
                目标题量
              </label>
              <input
                id="mistake-total"
                type="number"
                min={1}
                max={MAX_TOTAL_QUESTIONS}
                className="text-input"
                value={mistakeTotal}
                onChange={(event) =>
                  setMistakeTotal(
                    Math.min(
                      MAX_TOTAL_QUESTIONS,
                      Math.max(1, Number(event.target.value) || 1),
                    ),
                  )
                }
              />
            </div>
            <button
              type="button"
              className="primary-btn px-4 py-2 text-xs"
              onClick={handleCustomMistakeQuiz}
              disabled={Boolean(busy) || loadingMistakes || filteredMistakes.length === 0}
            >
              {busy === 'mistake-all' ? (
                <Loader2 className="size-4 animate-spin" aria-hidden="true" />
              ) : (
                <RotateCcw className="size-4" aria-hidden="true" />
              )}
              生成重练卷
            </button>
          </div>
          <p className="mt-2 text-xs leading-5 text-zinc-500">
            已匹配 {filteredMistakes.length} 道错题 · 目标 {mistakeTotal} 道 ·
            可用错题不足时按原题型比例生成变形题。
          </p>
        </div>
      )}

      {documents.length > 1 && (
        <div className="mt-4">
          <span className="field-label">资料范围</span>
          <ul className="flex flex-wrap gap-1.5">
            {documents.map((doc) => {
              const chosen = docIds.includes(doc.doc_id)
              return (
                <li key={doc.doc_id}>
                  <button
                    type="button"
                    className={`rounded-lg border px-2.5 py-1.5 text-xs transition ${
                      chosen
                        ? 'border-emerald-500 bg-emerald-50 text-emerald-800'
                        : 'border-zinc-300 bg-white text-zinc-600 hover:border-zinc-400'
                    }`}
                    onClick={() => toggleDoc(doc.doc_id)}
                    aria-pressed={chosen}
                  >
                    {doc.title}
                  </button>
                </li>
              )
            })}
          </ul>
          <p className="mt-1.5 text-xs text-zinc-500">
            不选则遍历全部文档。
          </p>
        </div>
      )}

      <div className="mt-4">
        <label htmlFor="topic-filter" className="field-label">
          知识点关键词
        </label>
        <div className="relative">
          <Search
            className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-zinc-400"
            aria-hidden="true"
          />
          <input
            id="topic-filter"
            className="text-input pl-9"
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
            placeholder="筛选关键词或板块"
          />
        </div>
      </div>

      {loadingTopics && topics.length === 0 ? (
        <p className="mt-4 flex items-center gap-2 text-sm text-zinc-500">
          <Loader2 className="size-4 animate-spin" aria-hidden="true" />
          正在从知识库提炼知识点
        </p>
      ) : (
        <div className="mt-3 max-h-72 space-y-4 overflow-y-auto rounded-lg border border-zinc-200 bg-zinc-50 p-3">
          {grouped.length === 0 && (
            <p className="text-sm text-zinc-500">没有匹配的关键词。</p>
          )}
          {grouped.map(([category, items]) => {
            const allChosen = items.every((item) => selected.includes(item.keyword))
            return (
              <div key={category}>
                <div className="mb-2 flex items-center justify-between gap-2">
                  <span className="text-xs font-semibold text-zinc-700">{category}</span>
                  <button
                    type="button"
                    className="text-xs font-medium text-emerald-700 transition hover:text-emerald-800"
                    onClick={() => toggleCategory(category, items)}
                  >
                    {allChosen ? '取消整组' : '选择整组'}
                  </button>
                </div>
                <ul className="flex flex-wrap gap-1.5">
                  {items.map((topic) => {
                    const chosen = selected.includes(topic.keyword)
                    return (
                      <li key={topic.keyword}>
                        <button
                          type="button"
                          className={`inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs transition ${
                            chosen
                              ? 'border-emerald-500 bg-emerald-600 text-white'
                              : 'border-zinc-300 bg-white text-zinc-700 hover:border-emerald-400'
                          }`}
                          onClick={() => toggleTopic(topic.keyword)}
                          aria-pressed={chosen}
                        >
                          {chosen && <Check className="size-3.5" aria-hidden="true" />}
                          {topic.keyword}
                        </button>
                      </li>
                    )
                  })}
                </ul>
              </div>
            )
          })}
        </div>
      )}

      <div className="mt-4 grid grid-cols-3 gap-3">
        <div>
          <label htmlFor="count-single" className="field-label">
            单选题
          </label>
          <input
            id="count-single"
            type="number"
            min={0}
            max={MAX_TOTAL_QUESTIONS}
            className="text-input"
            value={single}
            onChange={(event) => setSingle(Number(event.target.value) || 0)}
          />
        </div>
        <div>
          <label htmlFor="count-multiple" className="field-label">
            多选题
          </label>
          <input
            id="count-multiple"
            type="number"
            min={0}
            max={MAX_TOTAL_QUESTIONS}
            className="text-input"
            value={multiple}
            onChange={(event) => setMultiple(Number(event.target.value) || 0)}
          />
        </div>
        <div>
          <label htmlFor="count-judge" className="field-label">
            判断题
          </label>
          <input
            id="count-judge"
            type="number"
            min={0}
            max={MAX_TOTAL_QUESTIONS}
            className="text-input"
            value={judge}
            onChange={(event) => setJudge(Number(event.target.value) || 0)}
          />
        </div>
      </div>

      <p className={`mt-2 text-xs ${overLimit ? 'font-medium text-red-600' : 'text-zinc-500'}`}>
        共 {total} 道，一次最多 {MAX_TOTAL_QUESTIONS} 道（默认 20 道：单选 10 · 多选 6 · 判断 4）
        {overLimit ? '，请调低题数' : ''}
      </p>

      <div className="mt-4">
        <span className="field-label">难度</span>
        <div className="inline-flex rounded-lg border border-zinc-300 bg-zinc-100 p-1">
          {DIFFICULTIES.map((item) => (
            <button
              key={item.value}
              type="button"
              className={`rounded-md px-4 py-1.5 text-sm font-medium transition ${
                difficulty === item.value
                  ? 'bg-white text-zinc-900 shadow-sm'
                  : 'text-zinc-500 hover:text-zinc-700'
              }`}
              onClick={() => setDifficulty(item.value)}
            >
              {item.label}
            </button>
          ))}
        </div>
      </div>

      {error && <p className="mt-3 text-sm leading-5 text-red-600">{error}</p>}

      <button
        type="button"
        className="primary-btn mt-4 w-full py-3"
        onClick={handleGenerate}
        disabled={selected.length === 0 || total < 1 || overLimit || Boolean(busy)}
      >
        {busy === 'generate' ? (
          <Loader2 className="size-5 animate-spin" aria-hidden="true" />
        ) : (
          <FilePlus2 className="size-5" aria-hidden="true" />
        )}
        {busy === 'generate'
          ? '正在依据资料命题'
          : `生成试卷（已选 ${selected.length} 个知识点 · ${total} 道题）`}
      </button>
    </section>
  )
}
