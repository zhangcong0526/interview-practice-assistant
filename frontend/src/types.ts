export interface UploadInitResponse {
  upload_id: string
  chunk_size: number
  total_chunks: number
}

export interface UploadCompleteResponse {
  file_id: string
  filename: string
  size: number
}

export interface TranscriptSegment {
  start: number
  end: number
  text: string
}

export interface TranscriptResult {
  text: string
  segments: TranscriptSegment[]
  duration: number
  original_size: number
  compressed_size: number
  segment_count: number
}

export type JobStatus = 'queued' | 'running' | 'done' | 'error'

export interface JobState {
  job_id: string
  file_id?: string
  status: JobStatus
  stage?: string
  progress?: number
  result?: TranscriptResult
  error?: string
}

export interface JobCreateResponse {
  job_id: string
}

export interface DimensionScores {
  relevance: number
  structure: number
  specificity: number
  metrics: number
  conciseness: number
}

export interface QuestionAnalysis {
  question: string
  answer_summary: string
  scores: DimensionScores
  overall_score: number
  strengths: string[]
  issues: string[]
  suggestion: string
  reference_answer: string
}

export interface OverallAnalysis {
  score: number
  summary: string
  strengths: string[]
  weaknesses: string[]
}

export interface AnalysisReport {
  overall: OverallAnalysis
  questions: QuestionAnalysis[]
  practice_questions: string[]
}

export interface AnalyzeRequest {
  transcript: string
  jd: string
  resume: string
  knowledge?: string
  resume_id?: string
}

export type KnowledgeSourceType = 'local_file' | 'link' | 'paste' | 'feishu' | 'tencent' | string

export interface KnowledgeDocument {
  doc_id: string
  title: string
  source_type: KnowledgeSourceType
  source_url: string
  chunk_count: number
  char_count: number
  created_at: number
}

export interface KnowledgeChunk {
  chunk_id: string
  doc_id: string
  title: string
  source_type: KnowledgeSourceType
  source_url: string
  text: string
  score: number
  chunk_count: number
}

export type InterviewRole = 'interviewer' | 'candidate'

export interface InterviewMessage {
  role: InterviewRole
  content: string
}

export interface InterviewTurnRequest {
  jd: string
  resume: string
  knowledge?: string
  history: InterviewMessage[]
  asked_count: number
  max_questions: number
  resume_id?: string
  role_key?: string
}

export interface InterviewRoleOption {
  key: string
  name: string
  summary: string
  default_jd: string
  focus_areas: string[]
  search_query: string
}

export interface InterviewTurnResponse {
  speech: string
  is_new_question: boolean
  is_final: boolean
  note: string
}

export interface ResumeProject {
  name: string
  role: string
  period: string
  summary: string
  tech_stack: string[]
  achievements: string[]
  metrics: string[]
  gaps: string[]
}

export interface ResumeExperience {
  company: string
  title: string
  period: string
  summary: string
}

export interface ResumeProfile {
  name: string
  headline: string
  years_of_experience: number
  skills: string[]
  projects: ResumeProject[]
  experiences: ResumeExperience[]
  education: string
  highlights: string[]
  risk_points: string[]
}

export interface ResumeRecord {
  resume_id: string
  title: string
  source_type: string
  char_count: number
  created_at: number
  profile: ResumeProfile
  raw_text: string
}

export interface ResumeSummary {
  resume_id: string
  title: string
  source_type: string
  char_count: number
  created_at: number
  is_active: boolean
  name: string
  headline: string
  project_count: number
}

export interface ResumeAdviceSuggestion {
  priority: 'high' | 'medium' | 'low'
  target: string
  issue: string
  suggestion: string
  example: string
  evidence: string[]
}

export interface ResumeAdvice {
  resume_id: string
  char_count: number
  suggestions: ResumeAdviceSuggestion[]
  question_count: number
  weak_topic_count: number
  created_at: number
}

export interface ExpressionQuestion {
  question: string
  label: string
  source: string
  section: string
  role_key: string
  role_name: string
}

export interface ExpressionMetrics {
  duration_sec: number
  char_count: number
  sentence_count: number
  rate_cpm: number
  fillers: Record<string, number>
  crutches: Record<string, number>
  hedges: Record<string, number>
  filler_total: number
  crutch_total: number
  hedge_total: number
  fillers_per_min: number
  hedges_per_min: number
  restarts: string[]
  restart_count: number
  structure_markers: string[]
  scores: {
    fluency: number
    structure: number
    confidence: number
  }
}

export interface ExpressionCoach {
  summary: string
  strengths: string[]
  fixes: string[]
  example: string
  next_focus: string
  mindset_tip: string
}

export interface ExpressionSession {
  session_id: string
  created_at: number
  practice_date: string
  role_key: string
  role_name: string
  question: string
  question_label: string
  transcript: string
  metrics: ExpressionMetrics
  coach: ExpressionCoach
}

export interface ExpressionProgress {
  total_sessions: number
  practice_days: number
  streak_days: number
  today_count: number
  daily_goal: number
  averages: { fluency?: number; structure?: number; confidence?: number }
  trend: Array<{
    date: string
    created_at: number
    fluency: number
    structure: number
    confidence: number
    rate_cpm: number
    fillers_per_min: number
  }>
}

export type QuestionType = 'single' | 'multiple' | 'judge'
export type Difficulty = 'easy' | 'medium' | 'hard'
export type MasteryLevel = 'beginner' | 'developing' | 'proficient' | 'mastered'

export interface QuizTopic {
  keyword: string
  aliases: string[]
  category: string
  weight: number
}

export interface QuizOption {
  key: string
  text: string
}

export interface QuizQuestion {
  question_id: string
  type: QuestionType
  stem: string
  options: QuizOption[]
  topic: string
  difficulty: Difficulty
  source_title: string
}

export interface QuizPaper {
  paper_id: string
  title: string
  keywords: string[]
  doc_titles: string[]
  difficulty: string
  questions: QuizQuestion[]
  created_at: number
}

export interface GradedQuestion {
  question_id: string
  type: QuestionType
  stem: string
  options: QuizOption[]
  topic: string
  difficulty: Difficulty
  user_answer: string[]
  correct_answer: string[]
  is_correct: boolean
  explanation: string
  source_title: string
  user_answer_text: string
  correct_answer_text: string
}

export interface WeakTopic {
  topic: string
  diagnosis: string
  study_points: string[]
  next_actions: string[]
}

export interface QuizReview {
  summary: string
  mastery_level: MasteryLevel
  can_advance: boolean
  advance_reason: string
  weak_topics: WeakTopic[]
  study_plan: string[]
  encouragement: string
  review_error: string
}

export interface QuizAttempt {
  attempt_id: string
  paper_id: string
  paper_title: string
  score: number
  accuracy: number
  correct_count: number
  total: number
  questions: GradedQuestion[]
  review: QuizReview
  created_at: number
}

export interface AttemptSummary {
  attempt_id: string
  paper_id: string
  paper_title: string
  score: number
  correct_count: number
  total: number
  created_at: number
}

export interface MistakeItem {
  key: string
  topic: string
  type: QuestionType
  stem: string
  options: QuizOption[]
  correct_answer: string[]
  correct_answer_text: string
  user_answer_text: string
  explanation: string
  source_title: string
  paper_title: string
  wrong_count: number
  correct_streak: number
  created_at: number
  last_seen_at: number
}

export interface TopicProgress {
  topic: string
  total: number
  correct: number
  accuracy: number
  streak: number
  level: MasteryLevel
}

export interface QuizProgress {
  topics: TopicProgress[]
  recent_attempts: AttemptSummary[]
  mistake_count: number
  answered_total: number
  correct_total: number
  overall_accuracy: number
}

export interface QuizGenerateRequest {
  keywords: string[]
  doc_ids?: string[]
  single: number
  multiple: number
  judge: number
  difficulty: string
  focus_weak?: boolean
}
