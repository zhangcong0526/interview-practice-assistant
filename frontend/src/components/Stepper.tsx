import { AudioLines, BrainCircuit, FileText, Upload } from 'lucide-react'

interface StepperProps {
  current: number
}

const steps = [
  { title: '上传录音', icon: Upload },
  { title: '语音转写', icon: AudioLines },
  { title: 'AI 分析', icon: BrainCircuit },
  { title: '查看报告', icon: FileText },
]

export function Stepper({ current }: StepperProps) {
  return (
    <ol className="grid grid-cols-2 gap-3 sm:grid-cols-4">
      {steps.map((step, index) => {
        const Icon = step.icon
        const isActive = index === current
        const isDone = index < current
        return (
          <li
            key={step.title}
            className={`flex items-center gap-2 rounded-lg border px-3 py-2.5 text-sm font-medium ${
              isActive
                ? 'border-emerald-300 bg-emerald-50 text-emerald-800'
                : isDone
                  ? 'border-zinc-200 bg-white text-zinc-600'
                  : 'border-zinc-200 bg-zinc-100 text-zinc-400'
            }`}
          >
            <Icon className="size-4 shrink-0" aria-hidden="true" />
            <span className="truncate">{step.title}</span>
          </li>
        )
      })}
    </ol>
  )
}
