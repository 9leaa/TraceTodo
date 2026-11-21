"""Application Composition Root (`AppContainer`) wiring config, storage, vector store, LLM, and services."""

from __future__ import annotations

from dataclasses import dataclass

from trace_memory.config import TraceMemoryConfig
from trace_memory.llm.mock_provider import DeterministicLLMProvider, SemanticHashEmbedder
from trace_memory.llm.openai_compat import OpenAICompatEmbedder, OpenAICompatLLMProvider
from trace_memory.llm.protocol import EmbeddingProvider, LLMProvider
from trace_memory.services.ask_service import AskService
from trace_memory.services.auth_service import AdminService, AuthService
from trace_memory.services.calendar_service import CalendarService
from trace_memory.services.change_service import ChangeService
from trace_memory.services.dream_service import DreamService
from trace_memory.services.focus_service import FocusService
from trace_memory.services.guard_service import (
    InputGuardService,
    RateLimitAndQuotaService,
    SystemSettingsStore,
)
from trace_memory.services.ingest_queue import IngestJobRepository, IngestWorkerPool
from trace_memory.services.ingest_service import IngestService
from trace_memory.services.markdown_sync import MarkdownSyncService
from trace_memory.services.media_service import MediaAttachmentService
from trace_memory.services.preferences_service import PreferencesService
from trace_memory.services.retrieval_service import RetrievalService
from trace_memory.services.sync_and_scheduler import (
    DailyTaskMaintenanceScheduler,
    SpaceSyncTracker,
)
from trace_memory.services.task_matrix_service import TaskMatrixService
from trace_memory.services.voice_service import VoiceTranscriptionService
from trace_memory.storage.database import Database
from trace_memory.storage.hybrid_search import HybridSearchIndex
from trace_memory.storage.repositories import (
    MemoryRepository,
    NoteRepository,
    RelationRepository,
    TraceRepository,
)
from trace_memory.storage.vector_store import (
    QdrantVectorStore,
    SQLiteVectorStore,
    VectorLifecycleService,
    VectorStore,
)


@dataclass
class AppContainer:
    """Single explicit dependency container shared across CLI, MCP, Web API, and tests."""

    config: TraceMemoryConfig
    db: Database
    note_repo: NoteRepository
    memory_repo: MemoryRepository
    relation_repo: RelationRepository
    trace_repo: TraceRepository
    vector_store: VectorStore
    embedder: EmbeddingProvider
    llm: LLMProvider
    vector_lifecycle: VectorLifecycleService
    search_index: HybridSearchIndex
    change_service: ChangeService
    ingest_service: IngestService
    task_matrix_service: TaskMatrixService
    retrieval_service: RetrievalService
    ask_service: AskService
    dream_service: DreamService
    markdown_sync: MarkdownSyncService
    settings_store: SystemSettingsStore
    input_guard: InputGuardService
    quota_service: RateLimitAndQuotaService
    auth_service: AuthService
    admin_service: AdminService
    job_repo: IngestJobRepository
    ingest_worker: IngestWorkerPool
    sync_tracker: SpaceSyncTracker
    daily_scheduler: DailyTaskMaintenanceScheduler
    preferences_service: PreferencesService
    focus_service: FocusService
    voice_service: VoiceTranscriptionService
    media_service: MediaAttachmentService
    calendar_service: CalendarService

    @classmethod
    def build(
        cls,
        config: TraceMemoryConfig | None = None,
        llm_override: LLMProvider | None = None,
        embedder_override: EmbeddingProvider | None = None,
        vector_store_override: VectorStore | None = None,
    ) -> AppContainer:
        cfg = config or TraceMemoryConfig.from_env()
        db = Database(cfg.sqlite_path)

        note_repo = NoteRepository(db)
        memory_repo = MemoryRepository(db)
        relation_repo = RelationRepository(db, memory_repo)
        trace_repo = TraceRepository(db)

        if embedder_override is not None:
            embedder = embedder_override
        elif cfg.llm_provider == "openai" and cfg.openai_api_key:
            embedder = OpenAICompatEmbedder(
                base_url=cfg.openai_base_url,
                api_key=cfg.openai_api_key,
                model=cfg.embedding_model,
                dimension=cfg.embedding_dim,
                timeout_seconds=cfg.llm_timeout_seconds,
            )
        else:
            embedder = SemanticHashEmbedder(dimension=cfg.embedding_dim)

        if llm_override is not None:
            llm = llm_override
        elif cfg.llm_provider == "openai" and cfg.openai_api_key:
            llm = OpenAICompatLLMProvider(
                base_url=cfg.openai_base_url,
                api_key=cfg.openai_api_key,
                model=cfg.llm_model,
                timeout_seconds=cfg.llm_timeout_seconds,
            )
        else:
            llm = DeterministicLLMProvider()

        if vector_store_override is not None:
            vector_store = vector_store_override
        elif cfg.vector_backend == "qdrant":
            vector_store = QdrantVectorStore(
                embedding_dim=cfg.embedding_dim,
                path=cfg.qdrant_path,
                url=cfg.qdrant_url,
                api_key=cfg.qdrant_api_key,
            )
        else:
            vector_store = SQLiteVectorStore(db)

        vector_lifecycle = VectorLifecycleService(vector_store=vector_store, embedder=embedder)
        search_index = HybridSearchIndex(
            note_repo=note_repo,
            memory_repo=memory_repo,
            vector_store=vector_store,
            embedder=embedder,
        )
        settings_store = SystemSettingsStore(db=db, config=cfg)
        input_guard = InputGuardService(settings=settings_store)
        quota_service = RateLimitAndQuotaService(db=db, settings=settings_store)
        auth_service = AuthService(
            db=db,
            config=cfg,
            settings=settings_store,
            guard=quota_service,
        )
        admin_service = AdminService(
            db=db,
            config=cfg,
            settings=settings_store,
            auth=auth_service,
            guard=quota_service,
        )
        sync_tracker = SpaceSyncTracker(db=db)
        preferences_service = PreferencesService(db=db, sync_tracker=sync_tracker)
        media_service = MediaAttachmentService(db=db, config=cfg, sync_tracker=sync_tracker)
        change_service = ChangeService(
            note_repo=note_repo,
            memory_repo=memory_repo,
            relation_repo=relation_repo,
            trace_repo=trace_repo,
            vector_lifecycle=vector_lifecycle,
            sync_tracker=sync_tracker,
            media_service=media_service,
        )
        job_repo = IngestJobRepository(db=db)
        ingest_service = IngestService(
            note_repo=note_repo,
            memory_repo=memory_repo,
            relation_repo=relation_repo,
            trace_repo=trace_repo,
            vector_lifecycle=vector_lifecycle,
            change_service=change_service,
            llm=llm,
            input_guard=input_guard,
            quota_service=quota_service,
            job_repo=job_repo,
            sync_tracker=sync_tracker,
            media_service=media_service,
        )
        ingest_worker = IngestWorkerPool(
            job_repo=job_repo,
            processor=ingest_service.process_persisted_note,
            worker_count=cfg.ingest_worker_count,
            llm_max_concurrency=cfg.llm_max_concurrency,
            max_retries=cfg.ingest_max_retries,
        )
        retrieval_service = RetrievalService(
            note_repo=note_repo,
            memory_repo=memory_repo,
            relation_repo=relation_repo,
            trace_repo=trace_repo,
            search_index=search_index,
            default_max_hops=cfg.graph_max_hops,
            default_max_nodes=cfg.graph_max_nodes,
            default_max_chars=cfg.graph_max_chars,
        )
        task_matrix_service = TaskMatrixService(
            memory_repo=memory_repo,
            change_service=change_service,
            note_repo=note_repo,
            vector_lifecycle=vector_lifecycle,
            llm=llm,
            retrieval_service=retrieval_service,
            trace_repo=trace_repo,
        )
        daily_scheduler = DailyTaskMaintenanceScheduler(
            db=db,
            task_matrix_service=task_matrix_service,
            sync_tracker=sync_tracker,
        )
        focus_service = FocusService(
            db=db,
            memory_repo=memory_repo,
            task_matrix_service=task_matrix_service,
            sync_tracker=sync_tracker,
        )
        calendar_service = CalendarService(
            db=db,
            memory_repo=memory_repo,
            task_matrix_service=task_matrix_service,
        )
        voice_service = VoiceTranscriptionService(config=cfg)
        ask_service = AskService(
            note_repo=note_repo,
            memory_repo=memory_repo,
            trace_repo=trace_repo,
            search_index=search_index,
            retrieval_service=retrieval_service,
            llm=llm,
            input_guard=input_guard,
            quota_service=quota_service,
            job_repo=job_repo,
            media_service=media_service,
        )
        dream_service = DreamService(
            memory_repo=memory_repo,
            trace_repo=trace_repo,
            change_service=change_service,
            llm=llm,
            default_max_calls=cfg.dream_max_llm_calls,
            default_max_tokens=cfg.dream_max_tokens,
            quota_service=quota_service,
        )
        markdown_sync = MarkdownSyncService(
            knowledge_root=cfg.knowledge_dir,
            note_repo=note_repo,
            memory_repo=memory_repo,
            relation_repo=relation_repo,
            trace_repo=trace_repo,
            vector_lifecycle=vector_lifecycle,
            change_service=change_service,
        )

        return cls(
            config=cfg,
            db=db,
            note_repo=note_repo,
            memory_repo=memory_repo,
            relation_repo=relation_repo,
            trace_repo=trace_repo,
            vector_store=vector_store,
            embedder=embedder,
            llm=llm,
            vector_lifecycle=vector_lifecycle,
            search_index=search_index,
            change_service=change_service,
            ingest_service=ingest_service,
            task_matrix_service=task_matrix_service,
            retrieval_service=retrieval_service,
            ask_service=ask_service,
            dream_service=dream_service,
            markdown_sync=markdown_sync,
            settings_store=settings_store,
            input_guard=input_guard,
            quota_service=quota_service,
            auth_service=auth_service,
            admin_service=admin_service,
            job_repo=job_repo,
            ingest_worker=ingest_worker,
            sync_tracker=sync_tracker,
            daily_scheduler=daily_scheduler,
            preferences_service=preferences_service,
            focus_service=focus_service,
            voice_service=voice_service,
            media_service=media_service,
            calendar_service=calendar_service,
        )

    def close(self) -> None:
        self.daily_scheduler.stop()
        self.ingest_worker.stop()
        self.db.close()
