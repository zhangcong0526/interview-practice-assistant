from pydantic import BaseModel, Field


class UploadInitRequest(BaseModel):
    filename: str
    total_size: int = Field(gt=0)
    content_type: str = ""


class UploadInitResponse(BaseModel):
    upload_id: str
    chunk_size: int
    total_chunks: int


class UploadCompleteResponse(BaseModel):
    file_id: str
    filename: str
    size: int


class TranscribeRequest(BaseModel):
    file_id: str


class JobCreateResponse(BaseModel):
    job_id: str


class AnalyzeRequest(BaseModel):
    transcript: str = Field(min_length=1)
    jd: str = ""
    resume: str = ""
    knowledge: str = ""
    resume_id: str = ""


class KnowledgeFileRequest(BaseModel):
    file_id: str


class KnowledgeTextRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    content: str = Field(min_length=1)
    source_type: str = "paste"
    source_url: str = ""


class KnowledgeUrlRequest(BaseModel):
    url: str
    title: str = ""
    source_type: str = "link"


class KnowledgeSearchQuery(BaseModel):
    q: str = Field(min_length=1, max_length=5000)
    limit: int = Field(default=6, ge=1, le=20)


class InterviewMessage(BaseModel):
    role: str
    content: str


class InterviewTurnRequest(BaseModel):
    jd: str = ""
    resume: str = ""
    knowledge: str = ""
    history: list[InterviewMessage] = Field(default_factory=list)
    asked_count: int = Field(default=0, ge=0)
    max_questions: int = Field(default=6, ge=1, le=20)
    resume_id: str = ""
    role_key: str = ""


class ResumeFileRequest(BaseModel):
    file_id: str
    title: str = ""


class ResumeTextRequest(BaseModel):
    title: str = ""
    content: str = Field(min_length=1)


class ResumeProfileUpdate(BaseModel):
    profile: dict


class TopicExtractRequest(BaseModel):
    doc_ids: list[str] = Field(default_factory=list)
    refresh: bool = False


class QuizGenerateRequest(BaseModel):
    keywords: list[str] = Field(default_factory=list)
    doc_ids: list[str] = Field(default_factory=list)
    single: int = Field(default=12, ge=0, le=30)
    multiple: int = Field(default=8, ge=0, le=30)
    judge: int = Field(default=5, ge=0, le=30)
    difficulty: str = "mixed"
    focus_weak: bool = True


class MistakeQuizRequest(BaseModel):
    limit: int = Field(default=8, ge=1, le=20)
    difficulty: str = "mixed"


class ExpressionQuestionRequest(BaseModel):
    role_key: str = "robot_hardware"


class ExpressionAnalyzeRequest(BaseModel):
    role_key: str = "robot_hardware"
    question: str = Field(min_length=1, max_length=500)
    question_label: str = ""
    transcript: str = Field(min_length=1, max_length=20000)
    duration_sec: float = Field(default=0, ge=0, le=1800)


class QuizSubmitRequest(BaseModel):
    paper_id: str = Field(min_length=1)
    answers: dict[str, list[str]] = Field(default_factory=dict)


class TTSRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    voice: str = ""
    rate: int = Field(default=0, ge=-50, le=50)
    pitch: int = Field(default=0, ge=-50, le=50)
