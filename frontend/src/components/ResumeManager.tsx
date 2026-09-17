import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  ClipboardPaste,
  FileUp,
  IdCard,
  Loader2,
  Sparkles,
  Star,
  Trash2,
} from 'lucide-react'
import { useEffect, useState } from 'react'
import {
  activateResume,
  deleteResume,
  getActiveResume,
  listResumes,
  pasteResume,
  uploadFile,
  uploadResumeFile,
} from '../api'
import type { ResumeRecord, ResumeSummary } from '../types'

interface ResumeManagerProps {
  activeResume: ResumeRecord | null
  onActiveResumeChange: (resume: ResumeRecord | null) => void
}

type ImportMode = 'file' | 'paste'

const RESUME_EXTENSIONS = ['.txt', '.md', '.markdown', '.pdf', '.docx', '.html', '.htm']
const MAX_RESUME_BYTES = 20 * 1024 * 1024

function isResumeFile(file: File): boolean {
  const name = file.name.toLowerCase()
  return RESUME_EXTENSIONS.some((extension) => name.endsWith(extension))
}

export function ResumeManager({
  activeResume,
  onActiveResumeChange,
}: ResumeManagerProps) {
  const [mode, setMode] = useState<ImportMode>('file')
  const [resumes, setResumes] = useState<ResumeSummary[]>([])
  const [file, setFile] = useState<File | null>(null)
  const [fileTitle, setFileTitle] = useState('')
  const [pasteTitle, setPasteTitle] = useState('')
  const [pasteContent, setPasteContent] = useState('')
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [expanded, setExpanded] = useState<string>('')
  const [showLibrary, setShowLibrary] = useState(false)

  const refreshList = async () => {
    try {
      setResumes(await listResumes())
    } catch {
      setResumes([])
    }
  }

  useEffect(() => {
    void refreshList()
  }, [activeResume?.resume_id])

  const chooseFile = (candidate: File | null) => {
    if (!candidate) return
    if (!isResumeFile(candidate)) {
      setError('简历请使用 PDF / DOCX / TXT / Markdown / HTML 格式。')
      return
    }
    if (candidate.size > MAX_RESUME_BYTES) {
      setError('简历文件超过 20MB，请精简后重试。')
      return
    }
    setError('')
    setFile(candidate)
    if (!fileTitle.trim()) {
      setFileTitle(candidate.name.replace(/\.[^.]+$/, ''))
    }
  }

  const handleFileImport = async () => {
    if (!file || busy) return
    setBusy('file')
    setError('')
    try {
      const uploaded = await uploadFile(file, () => undefined)
      const record = await uploadResumeFile(uploaded.file_id, fileTitle.trim())
      onActiveResumeChange(record)
      setFile(null)
      setFileTitle('')
      await refreshList()
    } catch (importError) {
      setError(importError instanceof Error ? importError.message : '简历解析失败。')
    } finally {
      setBusy('')
    }
  }

  const handlePasteImport = async () => {
    if (!pasteContent.trim() || busy) return
    setBusy('paste')
    setError('')
    try {
      const record = await pasteResume(pasteTitle.trim() || '我的简历', pasteContent.trim())
      onActiveResumeChange(record)
      setPasteTitle('')
      setPasteContent('')
      await refreshList()
    } catch (importError) {
      setError(importError instanceof Error ? importError.message : '简历解析失败。')
    } finally {
      setBusy('')
    }
  }

  const handleActivate = async (resumeId: string) => {
    if (busy) return
    setBusy(`activate:${resumeId}`)
    setError('')
    try {
      await activateResume(resumeId)
      onActiveResumeChange(await getActiveResume())
      await refreshList()
    } catch (activateError) {
      setError(activateError instanceof Error ? activateError.message : '切换简历失败。')
    } finally {
      setBusy('')
    }
  }

  const handleDelete = async (resumeId: string) => {
    if (busy) return
    setBusy(`delete:${resumeId}`)
    setError('')
    try {
      await deleteResume(resumeId)
      if (activeResume?.resume_id === resumeId) {
        onActiveResumeChange(await getActiveResume())
      }
      await refreshList()
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : '删除简历失败。')
    } finally {
      setBusy('')
    }
  }

  const profile = activeResume?.profile
  const otherResumes = resumes.filter(
    (item) => item.resume_id !== activeResume?.resume_id,
  )

  return (
    <section className="tool-card">
      <h2 className="flex items-center gap-2 text-base font-semibold text-zinc-900">
        <IdCard className="size-5 text-emerald-600" aria-hidden="true" />
        简历档案
      </h2>
      <p className="mt-1.5 text-xs leading-5 text-zinc-500">
        导入简历后自动抽取项目、量化数据和待深挖点，模拟面试与评分都以此为基础展开。
      </p>

      {profile && (
        <div className="mt-4 rounded-lg border border-emerald-200 bg-emerald-50/60 p-4">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="truncate text-sm font-semibold text-zinc-900">
                {profile.name || activeResume?.title || '我的简历'}
                {profile.years_of_experience > 0 && (
                  <span className="ml-2 text-xs font-medium text-emerald-700">
                    {profile.years_of_experience} 年经验
                  </span>
                )}
              </p>
              {profile.headline && (
                <p className="mt-1 text-xs leading-5 text-zinc-600">{profile.headline}</p>
              )}
            </div>
            <span className="flex shrink-0 items-center gap-1 rounded-lg bg-emerald-600 px-2 py-1 text-xs font-medium text-white">
              <CheckCircle2 className="size-3.5" aria-hidden="true" />
              使用中
            </span>
          </div>

          {profile.skills.length > 0 && (
            <ul className="mt-3 flex flex-wrap gap-1.5">
              {profile.skills.slice(0, 18).map((skill) => (
                <li
                  key={skill}
                  className="rounded-lg bg-white px-2 py-1 text-xs text-zinc-700 ring-1 ring-emerald-200"
                >
                  {skill}
                </li>
              ))}
            </ul>
          )}

          {profile.projects.length > 0 && (
            <ul className="mt-3 space-y-2">
              {profile.projects.map((project, index) => {
                const key = `${project.name}-${index}`
                const open = expanded === key
                return (
                  <li key={key} className="rounded-lg bg-white ring-1 ring-zinc-200">
                    <button
                      type="button"
                      className="flex w-full items-center gap-2 px-3 py-2 text-left"
                      onClick={() => setExpanded(open ? '' : key)}
                      aria-expanded={open}
                    >
                      {open ? (
                        <ChevronDown className="size-4 shrink-0 text-zinc-400" aria-hidden="true" />
                      ) : (
                        <ChevronRight className="size-4 shrink-0 text-zinc-400" aria-hidden="true" />
                      )}
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-sm font-medium text-zinc-800">
                          {project.name || `项目 ${index + 1}`}
                        </span>
                        {(project.role || project.period) && (
                          <span className="block truncate text-xs text-zinc-500">
                            {[project.role, project.period].filter(Boolean).join(' · ')}
                          </span>
                        )}
                      </span>
                      {project.gaps.length > 0 && (
                        <span className="shrink-0 rounded-lg bg-amber-100 px-1.5 py-0.5 text-xs font-medium text-amber-800">
                          {project.gaps.length} 待深挖
                        </span>
                      )}
                    </button>

                    {open && (
                      <div className="space-y-2 border-t border-zinc-100 px-3 py-2.5 text-xs leading-5 text-zinc-600">
                        {project.summary && <p>{project.summary}</p>}
                        {project.tech_stack.length > 0 && (
                          <p>
                            <span className="font-medium text-zinc-800">技术栈：</span>
                            {project.tech_stack.join('、')}
                          </p>
                        )}
                        {project.metrics.length > 0 && (
                          <p>
                            <span className="font-medium text-zinc-800">量化结果：</span>
                            {project.metrics.join('；')}
                          </p>
                        )}
                        {project.gaps.length > 0 && (
                          <p className="text-amber-800">
                            <span className="font-medium">面试官可能追问：</span>
                            {project.gaps.join('；')}
                          </p>
                        )}
                      </div>
                    )}
                  </li>
                )
              })}
            </ul>
          )}

          {profile.highlights.length > 0 && (
            <div className="mt-3 flex gap-2 text-xs leading-5 text-zinc-700">
              <Star className="mt-0.5 size-3.5 shrink-0 text-emerald-600" aria-hidden="true" />
              <p>{profile.highlights.join('；')}</p>
            </div>
          )}

          {profile.risk_points.length > 0 && (
            <div className="mt-2 flex gap-2 text-xs leading-5 text-amber-800">
              <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
              <p>{profile.risk_points.join('；')}</p>
            </div>
          )}
        </div>
      )}

      <div className="mt-4">
        <div className="inline-flex rounded-lg border border-zinc-300 bg-white p-1">
          <button
            type="button"
            className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition ${
              mode === 'file' ? 'bg-emerald-600 text-white' : 'text-zinc-600 hover:bg-zinc-50'
            }`}
            onClick={() => setMode('file')}
          >
            <FileUp className="size-3.5" aria-hidden="true" />
            上传简历
          </button>
          <button
            type="button"
            className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition ${
              mode === 'paste' ? 'bg-emerald-600 text-white' : 'text-zinc-600 hover:bg-zinc-50'
            }`}
            onClick={() => setMode('paste')}
          >
            <ClipboardPaste className="size-3.5" aria-hidden="true" />
            粘贴文本
          </button>
        </div>
      </div>

      {mode === 'file' && (
        <div className="mt-3 space-y-3">
          <label
            htmlFor="resume-file"
            className="flex cursor-pointer items-center gap-2 rounded-lg border border-dashed border-zinc-300 bg-zinc-50 px-3 py-3 text-xs text-zinc-600 transition hover:border-emerald-400 hover:bg-emerald-50/40"
          >
            <FileUp className="size-4 shrink-0 text-zinc-400" aria-hidden="true" />
            <span className="min-w-0 truncate">
              {file ? file.name : '选择简历文件（PDF / DOCX / TXT / Markdown）'}
            </span>
          </label>
          <input
            id="resume-file"
            type="file"
            className="hidden"
            accept={RESUME_EXTENSIONS.join(',')}
            onChange={(event) => chooseFile(event.target.files?.[0] ?? null)}
          />
          {file && (
            <input
              className="text-input"
              value={fileTitle}
              onChange={(event) => setFileTitle(event.target.value)}
              placeholder="档案名称，例如：测试工程师-2026"
              aria-label="简历档案名称"
            />
          )}
          <button
            type="button"
            className="primary-btn w-full"
            onClick={handleFileImport}
            disabled={!file || Boolean(busy)}
          >
            {busy === 'file' ? (
              <Loader2 className="size-4 animate-spin" aria-hidden="true" />
            ) : (
              <Sparkles className="size-4" aria-hidden="true" />
            )}
            {busy === 'file' ? 'AI 正在抽取关键信息' : '解析并设为当前简历'}
          </button>
          {busy === 'file' && (
            <p className="text-xs leading-5 text-zinc-500">
              内容较长的简历会自动分段解析，请稍候。中途失败时重新上传同一份简历会接着上次的进度继续。
            </p>
          )}
        </div>
      )}

      {mode === 'paste' && (
        <div className="mt-3 space-y-3">
          <input
            className="text-input"
            value={pasteTitle}
            onChange={(event) => setPasteTitle(event.target.value)}
            placeholder="档案名称，例如：测试工程师-2026"
            aria-label="简历档案名称"
          />
          <textarea
            className="text-input min-h-36"
            value={pasteContent}
            onChange={(event) => setPasteContent(event.target.value)}
            placeholder="粘贴简历正文，包含项目经历、技术栈和量化结果。"
            aria-label="简历正文"
          />
          <button
            type="button"
            className="primary-btn w-full"
            onClick={handlePasteImport}
            disabled={!pasteContent.trim() || Boolean(busy)}
          >
            {busy === 'paste' ? (
              <Loader2 className="size-4 animate-spin" aria-hidden="true" />
            ) : (
              <Sparkles className="size-4" aria-hidden="true" />
            )}
            {busy === 'paste' ? 'AI 正在抽取关键信息' : '解析并设为当前简历'}
          </button>
          {busy === 'paste' && (
            <p className="text-xs leading-5 text-zinc-500">
              内容较长的简历会自动分段解析，请稍候。中途失败时用同样的内容重试会接着上次的进度继续。
            </p>
          )}
        </div>
      )}

      {error && <p className="mt-3 text-sm leading-5 text-red-600">{error}</p>}

      {otherResumes.length > 0 && (
        <div className="mt-4 border-t border-zinc-100 pt-3">
          <button
            type="button"
            className="flex items-center gap-1.5 text-xs font-medium text-zinc-600 transition hover:text-zinc-900"
            onClick={() => setShowLibrary(!showLibrary)}
            aria-expanded={showLibrary}
          >
            {showLibrary ? (
              <ChevronDown className="size-3.5" aria-hidden="true" />
            ) : (
              <ChevronRight className="size-3.5" aria-hidden="true" />
            )}
            其他简历（{otherResumes.length}）
          </button>

          {showLibrary && (
            <ul className="mt-2 space-y-2">
              {otherResumes.map((item) => (
                <li
                  key={item.resume_id}
                  className="flex items-center justify-between gap-2 rounded-lg border border-zinc-200 bg-zinc-50 px-3 py-2"
                >
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium text-zinc-800">{item.title}</p>
                    <p className="truncate text-xs text-zinc-500">
                      {[item.name, item.headline].filter(Boolean).join(' · ') || '未识别姓名'} ·{' '}
                      {item.project_count} 个项目
                    </p>
                  </div>
                  <div className="flex shrink-0 items-center gap-1">
                    <button
                      type="button"
                      className="secondary-btn px-2.5 py-1.5 text-xs"
                      onClick={() => handleActivate(item.resume_id)}
                      disabled={Boolean(busy)}
                    >
                      {busy === `activate:${item.resume_id}` ? (
                        <Loader2 className="size-3.5 animate-spin" aria-hidden="true" />
                      ) : null}
                      启用
                    </button>
                    <button
                      type="button"
                      className="rounded-lg p-2 text-zinc-400 transition hover:bg-red-50 hover:text-red-600"
                      onClick={() => handleDelete(item.resume_id)}
                      disabled={Boolean(busy)}
                      aria-label={`删除 ${item.title}`}
                      title={`删除 ${item.title}`}
                    >
                      <Trash2 className="size-4" aria-hidden="true" />
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  )
}
