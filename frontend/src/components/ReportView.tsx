import {
  AlertTriangle,
  CheckCircle2,
  Download,
  RotateCcw,
  Sparkles,
} from 'lucide-react'
import type { AnalysisReport, QuestionAnalysis } from '../types'

interface ReportViewProps {
  report: AnalysisReport
  onRestart: () => void
}

const dimensions: Array<{ key: keyof QuestionAnalysis['scores']; label: string }> = [
  { key: 'relevance', label: '相关性' },
  { key: 'structure', label: '结构' },
  { key: 'specificity', label: '具体性' },
  { key: 'metrics', label: '量化' },
  { key: 'conciseness', label: '简洁' },
]

function toList(value: string[] | undefined): string[] {
  return Array.isArray(value) ? value.filter(Boolean) : []
}

function exportMarkdown(report: AnalysisReport) {
  const lines = [
    '# 面试复盘报告',
    '',
    `## 整体评分：${report.overall.score} / 100`,
    '',
    report.overall.summary,
    '',
    '## 优势',
    ...toList(report.overall.strengths).map((item) => `- ${item}`),
    '',
    '## 短板',
    ...toList(report.overall.weaknesses).map((item) => `- ${item}`),
    '',
    '## 逐题分析',
  ]

  report.questions.forEach((question, index) => {
    lines.push(
      `### ${index + 1}. ${question.question || `回答 ${index + 1}`}`,
      `回答概要：${question.answer_summary}`,
      `综合得分：${question.overall_score} / 10`,
      `建议：${question.suggestion}`,
      '',
      '参考回答：',
      question.reference_answer,
      '',
    )
  })

  lines.push(
    '## 模拟面试官追问',
    ...toList(report.practice_questions).map((item) => `- ${item}`),
  )
  const blob = new Blob([lines.join('\n')], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `面试复盘报告-${new Date().toISOString().slice(0, 10)}.md`
  link.click()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export function ReportView({ report, onRestart }: ReportViewProps) {
  return (
    <div className="space-y-5">
      <section className="tool-card">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <p className="text-sm font-medium text-zinc-500">整体评分</p>
            <div className="mt-1 flex items-end gap-2">
              <span className="text-5xl font-bold text-emerald-700">
                {report.overall.score}
              </span>
              <span className="pb-2 text-sm text-zinc-500">/ 100</span>
            </div>
          </div>
          <div className="flex gap-2">
            <button
              type="button"
              className="secondary-btn"
              onClick={() => exportMarkdown(report)}
            >
              <Download className="size-4" aria-hidden="true" />
              导出 Markdown
            </button>
            <button type="button" className="primary-btn" onClick={onRestart}>
              <RotateCcw className="size-4" aria-hidden="true" />
              再练一轮
            </button>
          </div>
        </div>
        <div className="mt-4 h-2 w-full overflow-hidden rounded-full bg-zinc-200">
          <div
            className="h-full rounded-full bg-emerald-600"
            style={{
              width: `${Math.max(0, Math.min(100, report.overall.score))}%`,
            }}
          />
        </div>
        <p className="mt-4 text-sm leading-6 text-zinc-700">{report.overall.summary}</p>
      </section>

      <div className="grid gap-4 md:grid-cols-2">
        <section className="tool-card">
          <h2 className="text-base font-semibold text-zinc-900">主要优势</h2>
          <ul className="mt-3 space-y-2 text-sm text-zinc-700">
            {toList(report.overall.strengths).map((item) => (
              <li key={item} className="flex gap-2">
                <CheckCircle2
                  className="mt-0.5 size-4 shrink-0 text-emerald-600"
                  aria-hidden="true"
                />
                <span>{item}</span>
              </li>
            ))}
          </ul>
        </section>
        <section className="tool-card">
          <h2 className="text-base font-semibold text-zinc-900">待补短板</h2>
          <ul className="mt-3 space-y-2 text-sm text-zinc-700">
            {toList(report.overall.weaknesses).map((item) => (
              <li key={item} className="flex gap-2">
                <AlertTriangle
                  className="mt-0.5 size-4 shrink-0 text-amber-500"
                  aria-hidden="true"
                />
                <span>{item}</span>
              </li>
            ))}
          </ul>
        </section>
      </div>

      <section className="space-y-4">
        <h2 className="text-lg font-semibold text-zinc-900">逐题分析</h2>
        {report.questions.map((question, index) => (
          <article key={`${question.question}-${index}`} className="tool-card">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="text-xs font-semibold text-emerald-700">
                  问题 {index + 1}
                </p>
                <h3 className="mt-1 text-base font-semibold text-zinc-900">
                  {question.question}
                </h3>
              </div>
              <span className="rounded-lg bg-emerald-50 px-3 py-1.5 text-sm font-semibold text-emerald-700">
                {question.overall_score} / 10
              </span>
            </div>

            {question.answer_summary && (
              <p className="mt-3 rounded-lg bg-zinc-50 p-3 text-sm leading-6 text-zinc-700">
                {question.answer_summary}
              </p>
            )}

            <dl className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {dimensions.map((dimension) => {
                const score = Number(question.scores?.[dimension.key] ?? 0)
                return (
                  <div key={dimension.key} className="rounded-lg border border-zinc-200 p-3">
                    <dt className="text-xs font-medium text-zinc-500">{dimension.label}</dt>
                    <dd className="mt-2 flex items-center gap-1">
                      {[1, 2, 3, 4, 5].map((value) => (
                        <span
                          key={value}
                          className={`size-2.5 rounded-full ${
                            value <= score ? 'bg-emerald-600' : 'bg-zinc-200'
                          }`}
                          aria-hidden="true"
                        />
                      ))}
                      <span className="ml-1 text-xs text-zinc-500">{score}/5</span>
                    </dd>
                  </div>
                )
              })}
            </dl>

            <div className="mt-4 grid gap-4 md:grid-cols-2">
              <div>
                <h4 className="text-sm font-semibold text-emerald-700">亮点</h4>
                <ul className="mt-2 space-y-1.5 text-sm text-zinc-700">
                  {toList(question.strengths).map((item) => (
                    <li key={item}>- {item}</li>
                  ))}
                </ul>
              </div>
              <div>
                <h4 className="text-sm font-semibold text-amber-600">问题</h4>
                <ul className="mt-2 space-y-1.5 text-sm text-zinc-700">
                  {toList(question.issues).map((item) => (
                    <li key={item}>- {item}</li>
                  ))}
                </ul>
              </div>
            </div>

            {question.suggestion && (
              <p className="mt-4 rounded-lg border border-emerald-100 bg-emerald-50 p-3 text-sm leading-6 text-emerald-900">
                {question.suggestion}
              </p>
            )}

            <details className="mt-4 rounded-lg border border-zinc-200 p-3">
              <summary className="cursor-pointer text-sm font-semibold text-zinc-800">
                查看参考回答
              </summary>
              <p className="mt-3 whitespace-pre-wrap text-sm leading-6 text-zinc-700">
                {question.reference_answer}
              </p>
            </details>
          </article>
        ))}
      </section>

      <section className="tool-card">
        <h2 className="flex items-center gap-2 text-lg font-semibold text-zinc-900">
          <Sparkles className="size-5 text-emerald-600" aria-hidden="true" />
          模拟面试官追问
        </h2>
        <ol className="mt-3 space-y-2 text-sm text-zinc-700">
          {toList(report.practice_questions).map((question, index) => (
            <li key={question} className="rounded-lg bg-zinc-50 p-3">
              <span className="mr-2 font-semibold text-zinc-500">{index + 1}.</span>
              {question}
            </li>
          ))}
        </ol>
      </section>
    </div>
  )
}
