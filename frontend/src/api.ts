import type {
  AnalysisReport,
  AnalyzeRequest,
  JobCreateResponse,
  JobState,
  LlmConfig,
  LlmProvider,
  KnowledgeChunk,
  KnowledgeDocument,
  MistakePaperRequest,
  InterviewRoleOption,
  InterviewTurnRequest,
  InterviewTurnResponse,
  MistakeItem,
  QuizAttempt,
  QuizGenerateRequest,
  QuizPaper,
  QuizProgress,
  QuizTopic,
  ResumeAdvice,
  ExpressionProgress,
  ExpressionQuestion,
  ExpressionSession,
  ResumeRecord,
  ResumeSummary,
  UploadCompleteResponse,
  UploadInitResponse,
} from './types'

const API_BASE = '/api'

async function readErrorMessage(response: Response): Promise<string> {
  try {
    const data = await response.json()
    if (typeof data?.detail === 'string') return data.detail
    const details = data?.detail
    if (Array.isArray(details)) {
      return details
        .map((item) => (item as { msg?: string })?.msg)
        .filter(Boolean)
        .join('；')
    }
  } catch {
    // Fall through to the HTTP status text.
  }
  return `请求失败（${response.status}）`
}

async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, init)
  if (!response.ok) throw new Error(await readErrorMessage(response))
  const text = await response.text()
  return (text ? JSON.parse(text) : undefined) as T
}

export async function initUpload(file: File): Promise<UploadInitResponse> {
  return apiFetch<UploadInitResponse>('/uploads/init', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      filename: file.name,
      total_size: file.size,
      content_type: file.type || 'application/octet-stream',
    }),
  })
}

function putChunk(
  uploadId: string,
  index: number,
  chunk: Blob,
  onChunkProgress: (loaded: number) => void,
): Promise<void> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('PUT', `${API_BASE}/uploads/${uploadId}/chunks/${index}`)
    xhr.setRequestHeader('Content-Type', 'application/octet-stream')
    xhr.upload.onprogress = (event) => onChunkProgress(event.loaded)
    xhr.onerror = () => reject(new Error('网络中断，分片上传失败'))
    xhr.onabort = () => reject(new Error('上传已取消'))
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve()
        return
      }
      let message = `分片上传失败（${xhr.status}）`
      try {
        const data = JSON.parse(xhr.responseText)
        if (typeof data?.detail === 'string') message = data.detail
      } catch {
        // Keep the default message.
      }
      reject(new Error(message))
    }
    xhr.send(chunk)
  })
}

async function completeUpload(uploadId: string): Promise<UploadCompleteResponse> {
  return apiFetch<UploadCompleteResponse>(`/uploads/${uploadId}/complete`, {
    method: 'POST',
  })
}

export interface UploadProgress {
  uploaded: number
  total: number
}

export async function uploadFile(
  file: File,
  onProgress: (progress: UploadProgress) => void,
): Promise<UploadCompleteResponse> {
  const init = await initUpload(file)
  let uploaded = 0

  for (let index = 0; index < init.total_chunks; index += 1) {
    const start = index * init.chunk_size
    const end = Math.min(start + init.chunk_size, file.size)
    const chunk = file.slice(start, end)
    let lastError: Error | null = null

    for (let attempt = 1; attempt <= 3; attempt += 1) {
      try {
        await putChunk(init.upload_id, index, chunk, (loaded) => {
          onProgress({ uploaded: start + loaded, total: file.size })
        })
        lastError = null
        break
      } catch (error) {
        lastError = error instanceof Error ? error : new Error(String(error))
        if (attempt < 3) {
          await new Promise((resolve) => setTimeout(resolve, attempt * 600))
        }
      }
    }

    if (lastError) throw lastError
    uploaded = end
    onProgress({ uploaded, total: file.size })
  }

  return completeUpload(init.upload_id)
}

export async function createTranscriptionJob(fileId: string): Promise<JobCreateResponse> {
  return apiFetch<JobCreateResponse>('/jobs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ file_id: fileId }),
  })
}

export function pollJob(
  jobId: string,
  onUpdate: (job: JobState) => void,
  intervalMs = 1500,
): Promise<JobState> {
  return new Promise((resolve, reject) => {
    const poll = async () => {
      try {
        const job = await apiFetch<JobState>(`/jobs/${jobId}`)
        onUpdate(job)
        if (job.status === 'done') {
          resolve(job)
          return
        }
        if (job.status === 'error') {
          reject(new Error(job.error || '转写任务失败'))
          return
        }
        setTimeout(poll, intervalMs)
      } catch (error) {
        reject(error instanceof Error ? error : new Error(String(error)))
      }
    }
    void poll()
  })
}

export async function analyze(request: AnalyzeRequest): Promise<AnalysisReport> {
  return apiFetch<AnalysisReport>('/analyze', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
  })
}

export async function getLlmConfig(): Promise<LlmConfig> {
  return apiFetch<LlmConfig>('/config/llm')
}

export async function updateLlmConfig(request: {
  provider: LlmProvider
  deepseek_api_key?: string
  deepseek_base_url?: string
  deepseek_model?: string
  ark_api_key?: string
  ark_base_url?: string
  ark_model?: string
  minimax_api_key?: string
  minimax_base_url?: string
  minimax_model?: string
  openai_api_key?: string
  openai_base_url?: string
  openai_model?: string
}): Promise<LlmConfig> {
  return apiFetch<LlmConfig>('/config/llm', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
  })
}

export async function testLlmConfig(request: {
  provider: LlmProvider
  api_key?: string
  base_url?: string
  model?: string
}): Promise<{ ok: boolean; message: string; latency_ms: number; model: string }> {
  return apiFetch('/config/llm/test', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
  })
}

export async function listKnowledgeDocuments(): Promise<KnowledgeDocument[]> {
  return apiFetch<KnowledgeDocument[]>('/knowledge/documents')
}

export async function ingestKnowledgeFile(
  fileId: string,
): Promise<KnowledgeDocument> {
  return apiFetch<KnowledgeDocument>('/knowledge/files', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ file_id: fileId }),
  })
}

export async function saveKnowledgeText(request: {
  title: string
  content: string
  source_type: string
  source_url: string
}): Promise<KnowledgeDocument> {
  return apiFetch<KnowledgeDocument>('/knowledge/text', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
  })
}

export async function importKnowledgeUrl(request: {
  url: string
  title: string
  source_type: string
}): Promise<KnowledgeDocument> {
  return apiFetch<KnowledgeDocument>('/knowledge/url', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
  })
}

export async function deleteKnowledgeDocument(docId: string): Promise<void> {
  return apiFetch<void>(`/knowledge/documents/${docId}`, {
    method: 'DELETE',
  })
}

export interface TTSVoice {
  id: string
  label: string
  gender: string
  desc: string
}

export async function listTTSVoices(): Promise<{
  voices: TTSVoice[]
  default: string
}> {
  return apiFetch<{ voices: TTSVoice[]; default: string }>('/tts/voices')
}

export async function synthesiseSpeech(request: {
  text: string
  voice: string
  rate?: number
  pitch?: number
}, signal?: AbortSignal): Promise<Blob> {
  const response = await fetch(`${API_BASE}/tts/speak`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
    signal,
  })
  if (!response.ok) {
    throw new Error(await readErrorMessage(response))
  }
  return response.blob()
}

/**
 * 流式语音合成：后端边合成边下发，首字节约 1 秒即可到达，
 * 而整段合成要等 2 到 4 秒。切换岗位时的起播等待主要省在这里。
 */
export async function streamSpeech(request: {
  text: string
  voice: string
  rate?: number
  pitch?: number
}, signal?: AbortSignal): Promise<Response> {
  const response = await fetch(`${API_BASE}/tts/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
    signal,
  })
  if (!response.ok) {
    throw new Error(await readErrorMessage(response))
  }
  return response
}

export async function searchKnowledge(
  query: string,
  limit = 6,
): Promise<KnowledgeChunk[]> {
  const params = new URLSearchParams({ q: query, limit: String(limit) })
  return apiFetch<KnowledgeChunk[]>(`/knowledge/search?${params.toString()}`)
}

export async function requestInterviewTurn(
  request: InterviewTurnRequest,
): Promise<InterviewTurnResponse> {
  return apiFetch<InterviewTurnResponse>('/interview/turn', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
  })
}

export async function listInterviewRoles(): Promise<InterviewRoleOption[]> {
  return apiFetch<InterviewRoleOption[]>('/interview/roles')
}

export async function uploadResumeFile(
  fileId: string,
  title: string,
): Promise<ResumeRecord> {
  return apiFetch<ResumeRecord>('/resume/files', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ file_id: fileId, title }),
  })
}

export async function pasteResume(
  title: string,
  content: string,
): Promise<ResumeRecord> {
  return apiFetch<ResumeRecord>('/resume/text', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title, content }),
  })
}

export async function listResumes(): Promise<ResumeSummary[]> {
  return apiFetch<ResumeSummary[]>('/resume')
}

export async function getActiveResume(): Promise<ResumeRecord | null> {
  return apiFetch<ResumeRecord | null>('/resume/active')
}

export async function generateResumeAdvice(refresh = false): Promise<ResumeAdvice> {
  return apiFetch<ResumeAdvice>(`/resume/active/advice${refresh ? '?refresh=true' : ''}`, {
    method: 'POST',
  })
}

export async function loadResumeAdvice(resumeId: string): Promise<ResumeAdvice | null> {
  try {
    return await apiFetch<ResumeAdvice>(`/resume/${resumeId}/advice`)
  } catch {
    return null
  }
}

export async function getResume(resumeId: string): Promise<ResumeRecord> {
  return apiFetch<ResumeRecord>(`/resume/${resumeId}`)
}

export async function activateResume(resumeId: string): Promise<void> {
  await apiFetch<{ resume_id: string }>(`/resume/${resumeId}/activate`, {
    method: 'POST',
  })
}

export async function deleteResume(resumeId: string): Promise<void> {
  await apiFetch<{ deleted: string }>(`/resume/${resumeId}`, {
    method: 'DELETE',
  })
}

export async function extractQuizTopics(
  docIds: string[] = [],
  refresh = false,
): Promise<QuizTopic[]> {
  const data = await apiFetch<{ topics: QuizTopic[] }>('/quiz/topics', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ doc_ids: docIds, refresh }),
  })
  return data.topics ?? []
}

export async function generateQuizPaper(
  request: QuizGenerateRequest,
): Promise<QuizPaper> {
  return apiFetch<QuizPaper>('/quiz/papers', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
  })
}

export async function generateMistakePaper(
  request: MistakePaperRequest,
): Promise<QuizPaper> {
  return apiFetch<QuizPaper>('/quiz/papers/from-mistakes', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      limit: request.limit ?? 8,
      difficulty: request.difficulty ?? 'mixed',
      scope: request.scope,
      attempt_id: request.attempt_id ?? '',
    }),
  })
}

export async function submitQuiz(
  paperId: string,
  answers: Record<string, string[]>,
): Promise<QuizAttempt> {
  return apiFetch<QuizAttempt>('/quiz/submit', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ paper_id: paperId, answers }),
  })
}

export async function listMistakes(): Promise<MistakeItem[]> {
  return apiFetch<MistakeItem[]>('/quiz/mistakes')
}

export async function deleteMistake(key: string): Promise<void> {
  await apiFetch<{ deleted: string }>(
    `/quiz/mistakes/${encodeURIComponent(key)}`,
    { method: 'DELETE' },
  )
}

export async function getQuizProgress(): Promise<QuizProgress> {
  return apiFetch<QuizProgress>('/quiz/progress')
}

export async function getExpressionQuestion(roleKey: string): Promise<ExpressionQuestion> {
  return apiFetch<ExpressionQuestion>('/expression/question', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ role_key: roleKey }),
  })
}

export async function transcribeExpressionClip(blob: Blob): Promise<{
  text: string
  duration: number
  language: string
}> {
  const form = new FormData()
  form.append('file', blob, 'answer.webm')
  const response = await fetch(`${API_BASE}/expression/transcribe`, {
    method: 'POST',
    body: form,
  })
  if (!response.ok) {
    throw new Error(await readErrorMessage(response))
  }
  return response.json()
}

export async function analyzeExpression(payload: {
  role_key: string
  question: string
  question_label: string
  practice_mode: 'read' | 'keywords' | 'blind'
  transcript: string
  duration_sec: number
}): Promise<ExpressionSession> {
  return apiFetch<ExpressionSession>('/expression/analyze', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export async function getExpressionProgress(): Promise<ExpressionProgress> {
  return apiFetch<ExpressionProgress>('/expression/progress')
}
