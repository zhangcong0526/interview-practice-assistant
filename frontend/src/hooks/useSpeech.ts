import { useCallback, useEffect, useRef, useState } from 'react'

import { streamSpeech, synthesiseSpeech } from '../api'

// 浏览器语音识别在标准 DOM 类型里缺失，这里按实际用到的字段做最小声明。
interface SpeechRecognitionAlternativeLike {
  transcript: string
}

interface SpeechRecognitionResultLike {
  isFinal: boolean
  0: SpeechRecognitionAlternativeLike
}

interface SpeechRecognitionEventLike {
  resultIndex: number
  results: {
    length: number
    [index: number]: SpeechRecognitionResultLike
  }
}

interface SpeechRecognitionLike {
  lang: string
  continuous: boolean
  interimResults: boolean
  maxAlternatives: number
  start: () => void
  stop: () => void
  abort: () => void
  onresult: ((event: SpeechRecognitionEventLike) => void) | null
  onerror: ((event: { error: string }) => void) | null
  onend: (() => void) | null
}

type SpeechRecognitionCtor = new () => SpeechRecognitionLike

function getRecognitionCtor(): SpeechRecognitionCtor | null {
  if (typeof window === 'undefined') return null
  const scope = window as unknown as {
    SpeechRecognition?: SpeechRecognitionCtor
    webkitSpeechRecognition?: SpeechRecognitionCtor
  }
  return scope.SpeechRecognition ?? scope.webkitSpeechRecognition ?? null
}

export interface UseSpeechOptions {
  lang?: string
  onFinalText?: (text: string) => void
  voice?: string
  rate?: number
}

export function useSpeech({
  lang = 'zh-CN',
  onFinalText,
  voice = '',
  rate = 0,
}: UseSpeechOptions = {}) {
  const [isListening, setIsListening] = useState(false)
  const [isSpeaking, setIsSpeaking] = useState(false)
  const [interimText, setInterimText] = useState('')
  const [speechError, setSpeechError] = useState('')
  const [usingFallbackVoice, setUsingFallbackVoice] = useState(false)

  const recognitionRef = useRef<SpeechRecognitionLike | null>(null)
  const finalTextRef = useRef('')
  const shouldRestartRef = useRef(false)
  const onFinalTextRef = useRef(onFinalText)
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const audioUrlRef = useRef('')
  const voiceRef = useRef(voice)
  const rateRef = useRef(rate)
  // 每次播报领一个世代号。切换岗位或重新开始面试时世代号自增，
  // 此前仍在合成中的请求返回后会发现自己已过期，直接丢弃不播。
  const speechGenerationRef = useRef(0)
  const synthesisAbortRef = useRef<AbortController | null>(null)
  // 当前这条音频的等待者。被抢占时要主动唤醒，否则 await speak(...) 永远挂着。
  const playbackResolveRef = useRef<(() => void) | null>(null)
  // 输出设备保活。声卡（尤其蓝牙耳机）在音频流停止后会进入低功耗，
  // 重新出声要几百毫秒到数秒，这段时间 audio 元素已在推进，开头就被吃掉。
  // 频繁切换岗位会反复打断播放，每次都触发一次冷启动，所以这里改为
  // 用一条持续运行的静音轨道把输出链路一直占住。
  const warmupContextRef = useRef<AudioContext | null>(null)
  const keepAliveRef = useRef<{ osc: OscillatorNode; gain: GainNode } | null>(null)
  // 流式播放期间持有的 MediaSource，切换播报时要主动断开，避免旧流继续追加数据。
  const mediaSourceRef = useRef<MediaSource | null>(null)

  useEffect(() => {
    voiceRef.current = voice
    rateRef.current = rate
  }, [voice, rate])

  useEffect(() => {
    onFinalTextRef.current = onFinalText
  }, [onFinalText])

  const recognitionSupported = getRecognitionCtor() !== null
  const synthesisSupported =
    typeof window !== 'undefined' && 'speechSynthesis' in window

  const ensureRecognition = useCallback((): SpeechRecognitionLike | null => {
    if (recognitionRef.current) return recognitionRef.current
    const Ctor = getRecognitionCtor()
    if (!Ctor) return null

    const recognition = new Ctor()
    recognition.lang = lang
    recognition.continuous = true
    recognition.interimResults = true
    recognition.maxAlternatives = 1

    recognition.onresult = (event) => {
      let interim = ''
      for (let i = event.resultIndex; i < event.results.length; i += 1) {
        const result = event.results[i]
        const text = result[0]?.transcript ?? ''
        if (result.isFinal) {
          finalTextRef.current += text
        } else {
          interim += text
        }
      }
      setInterimText(interim)
    }

    recognition.onerror = (event) => {
      if (event.error === 'no-speech' || event.error === 'aborted') return
      if (event.error === 'not-allowed') {
        shouldRestartRef.current = false
        setSpeechError('麦克风权限被拒绝，请在浏览器地址栏允许麦克风后重试。')
        setIsListening(false)
        return
      }
      setSpeechError(`语音识别出错：${event.error}`)
    }

    recognition.onend = () => {
      // continuous 模式在静音时也会自动结束，需要主动续上。
      if (shouldRestartRef.current) {
        try {
          recognition.start()
          return
        } catch {
          shouldRestartRef.current = false
        }
      }
      setIsListening(false)
    }

    recognitionRef.current = recognition
    return recognition
  }, [lang])

  const startListening = useCallback(() => {
    const recognition = ensureRecognition()
    if (!recognition) {
      setSpeechError('当前浏览器不支持语音识别，请使用 Chrome 或 Edge。')
      return
    }
    finalTextRef.current = ''
    setInterimText('')
    setSpeechError('')
    shouldRestartRef.current = true
    try {
      recognition.start()
      setIsListening(true)
    } catch {
      // start() 在已启动时会抛错，忽略即可。
      setIsListening(true)
    }
  }, [ensureRecognition])

  const stopListening = useCallback((): string => {
    shouldRestartRef.current = false
    const recognition = recognitionRef.current
    if (recognition) {
      try {
        recognition.stop()
      } catch {
        // 忽略未启动时的异常。
      }
    }
    setIsListening(false)
    const text = `${finalTextRef.current}${interimText}`.trim()
    finalTextRef.current = ''
    setInterimText('')
    return text
  }, [interimText])

  const releaseAudio = useCallback(() => {
    const audio = audioRef.current
    if (audio) {
      audio.onended = null
      audio.onerror = null
      audio.onpause = null
      audio.pause()
    }
    audioRef.current = null
    const mediaSource = mediaSourceRef.current
    mediaSourceRef.current = null
    if (mediaSource && mediaSource.readyState === 'open') {
      try {
        mediaSource.endOfStream()
      } catch {
        // 已经结束或正在更新时忽略。
      }
    }
    if (audioUrlRef.current) {
      URL.revokeObjectURL(audioUrlRef.current)
      audioUrlRef.current = ''
    }
    // 摘掉回调后事件不会再来，必须手动放行等待中的 Promise。
    const resolvePlayback = playbackResolveRef.current
    playbackResolveRef.current = null
    resolvePlayback?.()
  }, [])

  /** 让所有进行中的播报立即作废：世代号自增 + 中止未完成的合成请求。 */
  const invalidateSpeech = useCallback(() => {
    speechGenerationRef.current += 1
    synthesisAbortRef.current?.abort()
    synthesisAbortRef.current = null
    return speechGenerationRef.current
  }, [])

  /**
   * 唤醒音频输出设备。
   *
   * 声卡在空闲后会进入低功耗状态，蓝牙耳机还要重新协商 A2DP 链路，
   * 冷启动期间 audio 元素照常推进 currentTime，于是开头几秒有声音数据
   * 却没被真正送出去。这里先播一段极短的静音把链路建立起来。
   */
  const warmUpOutput = useCallback(async () => {
    if (typeof window === 'undefined') return
    const Ctor =
      window.AudioContext ??
      (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
    if (!Ctor) return
    try {
      const context = warmupContextRef.current ?? new Ctor()
      warmupContextRef.current = context
      // 标签页切到后台或系统休眠后 context 会被挂起，每次播报前都要确认。
      if (context.state === 'suspended') await context.resume()

      // 持续输出一条完全静音的轨道，让声卡链路一直处于打开状态。
      // 只建一次，之后反复播报和切换岗位都复用，避免每次重新冷启动。
      if (!keepAliveRef.current) {
        const gain = context.createGain()
        gain.gain.value = 0
        const osc = context.createOscillator()
        osc.frequency.value = 20
        osc.connect(gain)
        gain.connect(context.destination)
        osc.start()
        keepAliveRef.current = { osc, gain }
      }
    } catch {
      // 预热失败不影响播放本身，退化为原来的行为。
    }
  }, [])

  // 浏览器自带的 speechSynthesis 在 Windows 上多是 SAPI 老引擎，机械感重，
  // 仅在神经语音不可用时兜底。
  const speakWithBrowser = useCallback(
    (text: string, generation: number): Promise<void> => {
      if (!synthesisSupported) return Promise.resolve()
      return new Promise((resolve) => {
        window.speechSynthesis.cancel()
        if (generation !== speechGenerationRef.current) {
          resolve()
          return
        }
        const utterance = new SpeechSynthesisUtterance(text)
        utterance.lang = lang
        utterance.rate = 1
        utterance.pitch = 1
        const finish = () => resolve()
        utterance.onend = finish
        utterance.onerror = finish
        window.speechSynthesis.speak(utterance)
      })
    },
    [lang, synthesisSupported],
  )

  /** 等待音频播放结束（或被抢占释放）。 */
  const awaitPlayback = useCallback((audio: HTMLAudioElement): Promise<void> => {
    return new Promise<void>((resolve) => {
      const finish = () => {
        if (playbackResolveRef.current === finish) {
          playbackResolveRef.current = null
        }
        resolve()
      }
      playbackResolveRef.current = finish
      audio.onended = finish
      audio.onerror = finish
      audio.play().catch(finish)
    })
  }, [])

  /**
   * 流式播报：后端边合成边下发，浏览器用 MediaSource 边收边播。
   * 返回 false 表示环境不支持或流不可用，由调用方回落到整段合成。
   */
  const playStreamed = useCallback(
    async (content: string, generation: number, signal: AbortSignal): Promise<boolean> => {
      if (typeof window === 'undefined' || typeof window.MediaSource === 'undefined') return false
      if (!window.MediaSource.isTypeSupported('audio/mpeg')) return false

      const response = await streamSpeech(
        { text: content, voice: voiceRef.current, rate: rateRef.current },
        signal,
      )
      if (generation !== speechGenerationRef.current) return true
      const body = response.body
      if (!body) return false

      const mediaSource = new MediaSource()
      const url = URL.createObjectURL(mediaSource)
      const audio = new Audio(url)
      audio.preload = 'auto'
      audioUrlRef.current = url
      audioRef.current = audio
      mediaSourceRef.current = mediaSource
      setUsingFallbackVoice(false)

      const sourceOpen = new Promise<SourceBuffer | null>((resolve) => {
        mediaSource.addEventListener(
          'sourceopen',
          () => {
            try {
              resolve(mediaSource.addSourceBuffer('audio/mpeg'))
            } catch {
              resolve(null)
            }
          },
          { once: true },
        )
      })

      await warmUpOutput()
      const sourceBuffer = await sourceOpen
      if (generation !== speechGenerationRef.current) return true
      if (!sourceBuffer) return false

      const reader = body.getReader()
      const idle = () =>
        sourceBuffer.updating
          ? new Promise<void>((resolve) => {
              sourceBuffer.addEventListener('updateend', () => resolve(), { once: true })
            })
          : Promise.resolve()

      // 先喂进第一块再起播，之后的数据在播放过程中持续追加。
      const pump = (async () => {
        try {
          for (;;) {
            const { done, value } = await reader.read()
            if (done) break
            if (generation !== speechGenerationRef.current) return
            if (mediaSourceRef.current !== mediaSource) return
            await idle()
            if (mediaSource.readyState !== 'open') return
            sourceBuffer.appendBuffer(value)
          }
          await idle()
          if (mediaSourceRef.current === mediaSource && mediaSource.readyState === 'open') {
            mediaSource.endOfStream()
          }
        } catch {
          // 中途断流时保留已播内容，交由 ended/error 收尾。
        }
      })()

      // 等第一块落入缓冲区，避免 play() 时长度仍为 0 直接 ended。
      await new Promise<void>((resolve) => {
        if (audio.readyState >= 2) {
          resolve()
          return
        }
        let settled = false
        const done = () => {
          if (settled) return
          settled = true
          window.clearTimeout(timer)
          audio.removeEventListener('loadeddata', done)
          audio.removeEventListener('error', done)
          resolve()
        }
        const timer = window.setTimeout(done, 3000)
        audio.addEventListener('loadeddata', done)
        audio.addEventListener('error', done)
      })
      if (generation !== speechGenerationRef.current) return true

      await awaitPlayback(audio)
      await pump
      return true
    },
    [awaitPlayback, warmUpOutput],
  )

  /** 整段合成后播放：流式不可用时的兜底路径。 */
  const playWhole = useCallback(
    async (content: string, generation: number, signal: AbortSignal): Promise<void> => {
      const blob = await synthesiseSpeech(
        { text: content, voice: voiceRef.current, rate: rateRef.current },
        signal,
      )
      if (generation !== speechGenerationRef.current) return
      const url = URL.createObjectURL(blob)
      audioUrlRef.current = url
      const audio = new Audio(url)
      audio.preload = 'auto'
      audioRef.current = audio
      setUsingFallbackVoice(false)

      await warmUpOutput()
      if (generation !== speechGenerationRef.current) return
      if (audio.readyState < 3) {
        await new Promise<void>((resolve) => {
          let settled = false
          const done = () => {
            if (settled) return
            settled = true
            window.clearTimeout(timer)
            audio.removeEventListener('canplaythrough', done)
            audio.removeEventListener('error', done)
            resolve()
          }
          const timer = window.setTimeout(done, 1500)
          audio.addEventListener('canplaythrough', done)
          audio.addEventListener('error', done)
          audio.load()
        })
        if (generation !== speechGenerationRef.current) return
      }

      await awaitPlayback(audio)
    },
    [awaitPlayback, warmUpOutput],
  )

  const speak = useCallback(
    async (text: string): Promise<void> => {
      const content = text.trim()
      if (!content) return

      // 抢占式播报：作废上一轮，本轮领取新的世代号。
      const generation = invalidateSpeech()
      releaseAudio()
      if (synthesisSupported) window.speechSynthesis.cancel()
      const controller = new AbortController()
      synthesisAbortRef.current = controller
      setIsSpeaking(true)

      try {
        // 优先走流式：首字节约 1 秒即可起播，整段合成要等 2 到 4 秒。
        const streamed = await playStreamed(content, generation, controller.signal)
        if (generation !== speechGenerationRef.current) return
        if (!streamed) {
          await playWhole(content, generation, controller.signal)
        }
      } catch {
        if (generation !== speechGenerationRef.current) return
        // 网络异常或后端不可用时退回浏览器语音，保证面试不中断。
        setUsingFallbackVoice(true)
        await speakWithBrowser(content, generation)
      } finally {
        // 只有仍然属于当前世代时才清理状态，否则会误关新一轮的播报。
        if (generation === speechGenerationRef.current) {
          releaseAudio()
          synthesisAbortRef.current = null
          setIsSpeaking(false)
        }
      }
    },
    [
      invalidateSpeech,
      playStreamed,
      playWhole,
      releaseAudio,
      speakWithBrowser,
      synthesisSupported,
    ],
  )

  const stopSpeaking = useCallback(() => {
    invalidateSpeech()
    releaseAudio()
    if (synthesisSupported) window.speechSynthesis.cancel()
    setIsSpeaking(false)
  }, [invalidateSpeech, releaseAudio, synthesisSupported])

  useEffect(() => {
    return () => {
      shouldRestartRef.current = false
      recognitionRef.current?.abort()
      speechGenerationRef.current += 1
      synthesisAbortRef.current?.abort()
      playbackResolveRef.current?.()
      playbackResolveRef.current = null
      const keepAlive = keepAliveRef.current
      if (keepAlive) {
        try {
          keepAlive.osc.stop()
          keepAlive.osc.disconnect()
          keepAlive.gain.disconnect()
        } catch {
          // 已经停止时忽略。
        }
        keepAliveRef.current = null
      }
      warmupContextRef.current?.close().catch(() => {})
      warmupContextRef.current = null
      const audio = audioRef.current
      if (audio) {
        audio.onended = null
        audio.onerror = null
        audio.pause()
      }
      if (audioUrlRef.current) URL.revokeObjectURL(audioUrlRef.current)
      if (typeof window !== 'undefined' && 'speechSynthesis' in window) {
        window.speechSynthesis.cancel()
      }
    }
  }, [])

  return {
    isListening,
    isSpeaking,
    interimText,
    speechError,
    recognitionSupported,
    synthesisSupported,
    usingFallbackVoice,
    startListening,
    stopListening,
    speak,
    stopSpeaking,
    warmUpOutput,
    setSpeechError,
  }
}
