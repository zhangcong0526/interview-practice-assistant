import {
  AlertTriangle,
  FileAudio,
  Loader2,
  Mic,
  Square,
  Upload,
  UploadCloud,
} from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import type { DragEvent } from 'react'
import { uploadFile } from '../api'

interface ChunkedUploaderProps {
  onUploaded: (fileId: string, file: File) => void
}

const MAX_BYTES = 2 * 1024 * 1024 * 1024

function formatBytes(bytes: number): string {
  if (bytes >= 1024 * 1024 * 1024) {
    return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`
  }
  if (bytes >= 1024 * 1024) {
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`
  }
  return `${Math.max(1, Math.round(bytes / 1024))} KB`
}

function formatSeconds(seconds: number): string {
  const minutes = Math.floor(seconds / 60)
  const rest = seconds % 60
  return `${String(minutes).padStart(2, '0')}:${String(rest).padStart(2, '0')}`
}

function isRecordingFile(file: File): boolean {
  const name = file.name.toLowerCase()
  const extensions = [
    '.wav',
    '.mp3',
    '.m4a',
    '.aac',
    '.ogg',
    '.oga',
    '.flac',
    '.webm',
    '.mkv',
    '.mp4',
    '.mov',
  ]
  return (
    file.type.startsWith('audio/') ||
    file.type.startsWith('video/') ||
    extensions.some((extension) => name.endsWith(extension))
  )
}

export function ChunkedUploader({ onUploaded }: ChunkedUploaderProps) {
  const [file, setFile] = useState<File | null>(null)
  const [isDragging, setIsDragging] = useState(false)
  const [isRecording, setIsRecording] = useState(false)
  const [recordSeconds, setRecordSeconds] = useState(0)
  const [isUploading, setIsUploading] = useState(false)
  const [uploadedBytes, setUploadedBytes] = useState(0)
  const [error, setError] = useState('')
  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const chunksRef = useRef<Blob[]>([])
  const timerRef = useRef<number | null>(null)

  useEffect(() => {
    return () => {
      if (timerRef.current !== null) window.clearInterval(timerRef.current)
      streamRef.current?.getTracks().forEach((track) => track.stop())
      if (mediaRecorderRef.current?.state === 'recording') {
        mediaRecorderRef.current.stop()
      }
    }
  }, [])

  const chooseFile = (candidate: File | null) => {
    if (!candidate) return
    if (!isRecordingFile(candidate)) {
      setError('请选择音频或视频文件；视频会自动提取音轨。')
      return
    }
    if (candidate.size > MAX_BYTES) {
      setError('文件超过当前 2GB 上限，请先压缩或截取。')
      return
    }
    setError('')
    setFile(candidate)
    setUploadedBytes(0)
  }

  const startRecording = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const mimeType = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4'].find(
        (type) => typeof MediaRecorder !== 'undefined' && MediaRecorder.isTypeSupported(type),
      )
      const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined)
      chunksRef.current = []
      streamRef.current = stream
      mediaRecorderRef.current = recorder
      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) chunksRef.current.push(event.data)
      }
      recorder.onstop = () => {
        const recordedMimeType = recorder.mimeType || mimeType || 'audio/webm'
        const extension = recordedMimeType.includes('mp4') ? 'm4a' : 'webm'
        const recorded = new File(
          chunksRef.current,
          `面试练习-${Date.now()}.${extension}`,
          { type: recordedMimeType },
        )
        stream.getTracks().forEach((track) => track.stop())
        chooseFile(recorded)
      }
      recorder.start(1000)
      setRecordSeconds(0)
      setIsRecording(true)
      setError('')
      timerRef.current = window.setInterval(() => {
        setRecordSeconds((value) => value + 1)
      }, 1000)
    } catch {
      setError('无法访问麦克风，请检查浏览器权限。')
    }
  }

  const stopRecording = () => {
    if (timerRef.current !== null) window.clearInterval(timerRef.current)
    timerRef.current = null
    setIsRecording(false)
    mediaRecorderRef.current?.stop()
  }

  const handleUpload = async () => {
    if (!file || isUploading) return
    setIsUploading(true)
    setError('')
    setUploadedBytes(0)
    try {
      const result = await uploadFile(file, ({ uploaded }) => setUploadedBytes(uploaded))
      onUploaded(result.file_id, file)
    } catch (uploadError) {
      setError(uploadError instanceof Error ? uploadError.message : '上传失败，请重试。')
    } finally {
      setIsUploading(false)
    }
  }

  const onDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    setIsDragging(false)
    chooseFile(event.dataTransfer.files?.[0] ?? null)
  }

  const progressPercent = file
    ? Math.min(100, Math.round((uploadedBytes / file.size) * 100))
    : 0

  return (
    <section className="space-y-4">
      <div
        onDragOver={(event) => {
          event.preventDefault()
          setIsDragging(true)
        }}
        onDragLeave={() => setIsDragging(false)}
        onDrop={onDrop}
        className={`flex min-h-40 flex-col items-center justify-center gap-3 rounded-lg border-2 border-dashed p-6 text-center transition ${
          isDragging ? 'border-emerald-500 bg-emerald-50' : 'border-zinc-300 bg-zinc-50'
        }`}
      >
        <UploadCloud className="size-8 text-zinc-400" aria-hidden="true" />
        <div>
          <p className="text-sm font-semibold text-zinc-800">拖入录音或视频文件</p>
          <p className="mt-1 text-xs text-zinc-500">
            支持 WAV / MP3 / M4A / WebM / MP4，单文件最大 2GB
          </p>
        </div>
        <label className="secondary-btn cursor-pointer">
          <FileAudio className="size-4" aria-hidden="true" />
          选择文件
          <input
            type="file"
            className="sr-only"
            accept="audio/*,video/*,.wav,.mp3,.m4a,.aac,.ogg,.flac,.webm,.mkv,.mp4,.mov"
            onChange={(event) => chooseFile(event.target.files?.[0] ?? null)}
          />
        </label>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          className="primary-btn"
          onClick={startRecording}
          disabled={isRecording}
        >
          <Mic className="size-4" aria-hidden="true" />
          开始录音
        </button>
        <button
          type="button"
          className="secondary-btn"
          onClick={stopRecording}
          disabled={!isRecording}
        >
          <Square className="size-4" aria-hidden="true" />
          停止
        </button>
        {isRecording && (
          <span className="font-mono text-sm text-red-600" role="timer" aria-live="polite">
            {formatSeconds(recordSeconds)}
          </span>
        )}
      </div>

      {file && (
        <div className="space-y-3 rounded-lg border border-zinc-200 bg-zinc-50 p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="min-w-0">
              <p className="truncate text-sm font-semibold text-zinc-800">{file.name}</p>
              <p className="text-xs text-zinc-500">{formatBytes(file.size)}</p>
            </div>
            <button
              type="button"
              className="primary-btn"
              onClick={handleUpload}
              disabled={isUploading}
            >
              {isUploading ? (
                <Loader2 className="size-4 animate-spin" aria-hidden="true" />
              ) : (
                <Upload className="size-4" aria-hidden="true" />
              )}
              {isUploading ? '上传中' : '上传并转写'}
            </button>
          </div>
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
          <p className="text-xs text-zinc-500">
            {isUploading
              ? `已上传 ${formatBytes(uploadedBytes)} / ${formatBytes(file.size)}（${progressPercent}%，8MB 分片）`
              : uploadedBytes === file.size
                ? '上传完成'
                : '按 8MB 分片上传，网络波动时单个分片自动重试'}
          </p>
        </div>
      )}

      {error && (
        <p className="flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          <span>{error}</span>
        </p>
      )}
    </section>
  )
}
