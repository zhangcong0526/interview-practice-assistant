import {
  BookOpen,
  ClipboardPaste,
  FileUp,
  Link2,
  Loader2,
  Trash2,
  UploadCloud,
} from 'lucide-react'
import { useState } from 'react'
import {
  deleteKnowledgeDocument,
  importKnowledgeUrl,
  ingestKnowledgeFile,
  saveKnowledgeText,
  uploadFile,
} from '../api'
import type { KnowledgeDocument, KnowledgeSourceType } from '../types'

interface KnowledgeLibraryProps {
  documents: KnowledgeDocument[]
  onDocumentsChange: (documents: KnowledgeDocument[]) => void
}

type ImportMode = 'local' | 'link' | 'paste'
type Provider = 'feishu' | 'tencent' | 'other'

const MAX_BYTES = 2 * 1024 * 1024 * 1024
const DOCUMENT_EXTENSIONS = [
  '.txt',
  '.md',
  '.markdown',
  '.log',
  '.pdf',
  '.docx',
  '.pptx',
  '.xlsx',
  '.csv',
  '.json',
  '.xml',
  '.yaml',
  '.yml',
  '.toml',
  '.html',
  '.htm',
]

function formatBytes(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`
  return `${Math.max(1, Math.round(bytes / 1024))} KB`
}

function sourceLabel(sourceType: KnowledgeSourceType): string {
  if (sourceType === 'local_file') return '本地文档'
  if (sourceType === 'feishu') return '飞书文档'
  if (sourceType === 'tencent') return '腾讯文档'
  if (sourceType === 'link') return '在线链接'
  return '粘贴正文'
}

function isDocumentFile(file: File): boolean {
  const name = file.name.toLowerCase()
  return DOCUMENT_EXTENSIONS.some((extension) => name.endsWith(extension))
}

export function KnowledgeLibrary({
  documents,
  onDocumentsChange,
}: KnowledgeLibraryProps) {
  const [mode, setMode] = useState<ImportMode>('local')
  const [provider, setProvider] = useState<Provider>('feishu')
  const [localFile, setLocalFile] = useState<File | null>(null)
  const [uploadedBytes, setUploadedBytes] = useState(0)
  const [linkUrl, setLinkUrl] = useState('')
  const [linkTitle, setLinkTitle] = useState('')
  const [pasteTitle, setPasteTitle] = useState('')
  const [pasteContent, setPasteContent] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState('')

  const addDocument = (doc: KnowledgeDocument) => {
    onDocumentsChange([
      doc,
      ...documents.filter((item) => item.doc_id !== doc.doc_id),
    ])
  }

  const chooseLocalFile = (candidate: File | null) => {
    if (!candidate) return
    if (!isDocumentFile(candidate)) {
      setError('请选择 TXT / Markdown / PDF / DOCX / PPTX / XLSX / HTML 等文档。')
      return
    }
    if (candidate.size > MAX_BYTES) {
      setError('文件超过当前 2GB 上限。')
      return
    }
    setError('')
    setLocalFile(candidate)
    setUploadedBytes(0)
  }

  const handleLocalImport = async () => {
    if (!localFile || busy) return
    setBusy(`local:${localFile.name}`)
    setError('')
    try {
      const uploaded = await uploadFile(localFile, ({ uploaded }) =>
        setUploadedBytes(uploaded),
      )
      const doc = await ingestKnowledgeFile(uploaded.file_id)
      addDocument(doc)
      setLocalFile(null)
      setUploadedBytes(0)
    } catch (importError) {
      setError(importError instanceof Error ? importError.message : '文档导入失败。')
    } finally {
      setBusy(null)
    }
  }

  const handleLinkImport = async () => {
    if (!linkUrl.trim() || busy) return
    setBusy('link')
    setError('')
    try {
      const doc = await importKnowledgeUrl({
        url: linkUrl.trim(),
        title: linkTitle.trim(),
        source_type: provider,
      })
      addDocument(doc)
      setLinkUrl('')
      setLinkTitle('')
    } catch (importError) {
      setError(importError instanceof Error ? importError.message : '链接导入失败。')
    } finally {
      setBusy(null)
    }
  }

  const handlePasteImport = async () => {
    if (!pasteTitle.trim() || !pasteContent.trim() || busy) return
    setBusy('paste')
    setError('')
    try {
      const doc = await saveKnowledgeText({
        title: pasteTitle.trim(),
        content: pasteContent.trim(),
        source_type: provider === 'other' ? 'paste' : provider,
        source_url: '',
      })
      addDocument(doc)
      setPasteTitle('')
      setPasteContent('')
    } catch (importError) {
      setError(importError instanceof Error ? importError.message : '正文导入失败。')
    } finally {
      setBusy(null)
    }
  }

  const handleDelete = async (docId: string) => {
    try {
      await deleteKnowledgeDocument(docId)
      onDocumentsChange(documents.filter((item) => item.doc_id !== docId))
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : '删除文档失败。')
    }
  }

  const progressPercent = localFile
    ? Math.min(100, Math.round((uploadedBytes / localFile.size) * 100))
    : 0

  return (
    <section className="tool-card">
      <div className="flex items-center justify-between gap-3">
        <h2 className="flex items-center gap-2 text-base font-semibold text-zinc-900">
          <BookOpen className="size-5 text-emerald-600" aria-hidden="true" />
          测试知识库
        </h2>
        <span className="rounded-lg bg-zinc-100 px-2 py-1 text-xs font-medium text-zinc-500">
          {documents.length} 份
        </span>
      </div>

      <div className="mt-4 grid grid-cols-3 gap-2 rounded-lg bg-zinc-100 p-1">
        <button
          type="button"
          onClick={() => setMode('local')}
          className={`rounded-md px-2 py-2 text-sm font-semibold ${
            mode === 'local'
              ? 'bg-white text-zinc-900 shadow-sm'
              : 'text-zinc-500 hover:text-zinc-700'
          }`}
        >
          本地文档
        </button>
        <button
          type="button"
          onClick={() => setMode('link')}
          className={`rounded-md px-2 py-2 text-sm font-semibold ${
            mode === 'link'
              ? 'bg-white text-zinc-900 shadow-sm'
              : 'text-zinc-500 hover:text-zinc-700'
          }`}
        >
          文档链接
        </button>
        <button
          type="button"
          onClick={() => setMode('paste')}
          className={`rounded-md px-2 py-2 text-sm font-semibold ${
            mode === 'paste'
              ? 'bg-white text-zinc-900 shadow-sm'
              : 'text-zinc-500 hover:text-zinc-700'
          }`}
        >
          粘贴正文
        </button>
      </div>

      {mode !== 'local' && (
        <div className="mt-3 flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => setProvider('feishu')}
            className={`rounded-lg border px-3 py-1.5 text-xs font-semibold ${
              provider === 'feishu'
                ? 'border-emerald-300 bg-emerald-50 text-emerald-800'
                : 'border-zinc-200 bg-white text-zinc-600'
            }`}
          >
            飞书
          </button>
          <button
            type="button"
            onClick={() => setProvider('tencent')}
            className={`rounded-lg border px-3 py-1.5 text-xs font-semibold ${
              provider === 'tencent'
                ? 'border-emerald-300 bg-emerald-50 text-emerald-800'
                : 'border-zinc-200 bg-white text-zinc-600'
            }`}
          >
            腾讯
          </button>
          <button
            type="button"
            onClick={() => setProvider('other')}
            className={`rounded-lg border px-3 py-1.5 text-xs font-semibold ${
              provider === 'other'
                ? 'border-emerald-300 bg-emerald-50 text-emerald-800'
                : 'border-zinc-200 bg-white text-zinc-600'
            }`}
          >
            其他
          </button>
        </div>
      )}

      {mode === 'local' && (
        <div className="mt-4 space-y-3">
          <label className="flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-zinc-300 bg-zinc-50 p-4 text-center">
            <UploadCloud className="size-7 text-zinc-400" aria-hidden="true" />
            <span className="text-sm font-semibold text-zinc-800">
              {localFile ? localFile.name : '选择知识库文档'}
            </span>
            <span className="text-xs text-zinc-500">
              PDF / DOCX / PPTX / XLSX / MD / HTML，单文件最大 2GB
            </span>
            <input
              type="file"
              className="sr-only"
              accept={DOCUMENT_EXTENSIONS.join(',')}
              onChange={(event) => chooseLocalFile(event.target.files?.[0] ?? null)}
            />
          </label>

          {localFile && (
            <>
              <div
                className="h-2 w-full overflow-hidden rounded-full bg-zinc-200"
                role="progressbar"
                aria-valuenow={progressPercent}
                aria-valuemin={0}
                aria-valuemax={100}
              >
                <div
                  className="h-full rounded-full bg-emerald-600 transition-all"
                  style={{ width: `${progressPercent}%` }}
                />
              </div>
              <button
                type="button"
                className="primary-btn w-full"
                onClick={handleLocalImport}
                disabled={Boolean(busy)}
              >
                {busy?.startsWith('local:') ? (
                  <Loader2 className="size-4 animate-spin" aria-hidden="true" />
                ) : (
                  <FileUp className="size-4" aria-hidden="true" />
                )}
                {busy?.startsWith('local:')
                  ? `导入中 ${formatBytes(uploadedBytes)}`
                  : '导入并解析'}
              </button>
            </>
          )}
        </div>
      )}

      {mode === 'link' && (
        <div className="mt-4 space-y-3">
          <div>
            <label htmlFor="knowledge-url" className="field-label">
              公开链接
            </label>
            <input
              id="knowledge-url"
              className="text-input"
              value={linkUrl}
              onChange={(event) => setLinkUrl(event.target.value)}
              placeholder="https://example.feishu.cn/docx/..."
            />
          </div>
          <div>
            <label htmlFor="knowledge-link-title" className="field-label">
              显示名称
            </label>
            <input
              id="knowledge-link-title"
              className="text-input"
              value={linkTitle}
              onChange={(event) => setLinkTitle(event.target.value)}
              placeholder="可选，不填则使用页面标题或域名"
            />
          </div>
          <button
            type="button"
            className="primary-btn w-full"
            onClick={handleLinkImport}
            disabled={!linkUrl.trim() || Boolean(busy)}
          >
            {busy === 'link' ? (
              <Loader2 className="size-4 animate-spin" aria-hidden="true" />
            ) : (
              <Link2 className="size-4" aria-hidden="true" />
            )}
            {busy === 'link' ? '导入中' : '导入链接'}
          </button>
          <p className="text-xs leading-5 text-zinc-500">
            链接需为公开可访问的 HTTPS 页面；需要登录或动态渲染的飞书/腾讯文档请改用粘贴正文。
          </p>
        </div>
      )}

      {mode === 'paste' && (
        <div className="mt-4 space-y-3">
          <div>
            <label htmlFor="knowledge-paste-title" className="field-label">
              文档名称
            </label>
            <input
              id="knowledge-paste-title"
              className="text-input"
              value={pasteTitle}
              onChange={(event) => setPasteTitle(event.target.value)}
              placeholder="例如：接口自动化测试规范"
            />
          </div>
          <div>
            <label htmlFor="knowledge-paste-content" className="field-label">
              正文
            </label>
            <textarea
              id="knowledge-paste-content"
              className="text-input min-h-36"
              value={pasteContent}
              onChange={(event) => setPasteContent(event.target.value)}
              placeholder="粘贴飞书、腾讯文档或本地知识库中的正文。"
            />
          </div>
          <button
            type="button"
            className="primary-btn w-full"
            onClick={handlePasteImport}
            disabled={!pasteTitle.trim() || !pasteContent.trim() || Boolean(busy)}
          >
            {busy === 'paste' ? (
              <Loader2 className="size-4 animate-spin" aria-hidden="true" />
            ) : (
              <ClipboardPaste className="size-4" aria-hidden="true" />
            )}
            {busy === 'paste' ? '导入中' : '保存到知识库'}
          </button>
        </div>
      )}

      {error && <p className="mt-3 text-sm leading-5 text-red-600">{error}</p>}

      {documents.length > 0 && (
        <ul className="mt-4 space-y-2 border-t border-zinc-100 pt-3">
          {documents.map((doc) => (
            <li
              key={doc.doc_id}
              className="flex items-center justify-between gap-3 rounded-lg border border-zinc-200 bg-zinc-50 px-3 py-2"
            >
              <div className="min-w-0">
                <p className="truncate text-sm font-semibold text-zinc-800">{doc.title}</p>
                <p className="truncate text-xs text-zinc-500">
                  {sourceLabel(doc.source_type)} · {doc.chunk_count} 段 ·{' '}
                  {Math.max(1, Math.round(doc.char_count / 1000))}k 字
                </p>
              </div>
              <button
                type="button"
                className="rounded-lg p-2 text-zinc-400 transition hover:bg-red-50 hover:text-red-600"
                onClick={() => handleDelete(doc.doc_id)}
                aria-label={`删除 ${doc.title}`}
              >
                <Trash2 className="size-4" aria-hidden="true" />
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
