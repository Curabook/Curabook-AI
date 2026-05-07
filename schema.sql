-- ── Extensions ────────────────────────────────────────────────────────────────
create extension if not exists vector;

-- ── user_profiles ─────────────────────────────────────────────────────────────
create table if not exists user_profiles (
    user_id                      uuid primary key references auth.users(id) on delete cascade,
    first_name                   text,
    last_name                    text,
    age                          integer,
    date_of_birth                date,
    gender                       text,
    timezone                     text default 'UTC',
    role                         text not null default 'patient' check (role in ('patient','doctor','admin')),
    plan                         text default 'free',
    reports_remaining            integer default 1,
    razorpay_subscription_id     text,
    razorpay_pending_subscription text,
    razorpay_last_payment_id     text,
    health_persona_text          text,
    health_persona_updated_at    timestamptz,
    health_persona_marker_count  integer default 0,
    created_at                   timestamptz default now(),
    updated_at                   timestamptz default now()
);

-- ── conversations ──────────────────────────────────────────────────────────────
create table if not exists conversations (
    id         uuid primary key default gen_random_uuid(),
    user_id    uuid not null references auth.users(id) on delete cascade,
    title      text default 'New Chat',
    created_at timestamptz default now()
);

-- ── chats ──────────────────────────────────────────────────────────────────────
create table if not exists chats (
    id               uuid primary key default gen_random_uuid(),
    user_id          uuid not null references auth.users(id) on delete cascade,
    conversation_id  uuid not null references conversations(id) on delete cascade,
    role             text not null check (role in ('user','assistant')),
    content          text not null,
    is_phi           boolean default false,
    created_at       timestamptz default now()
);

-- ── health_markers ─────────────────────────────────────────────────────────────
create table if not exists health_markers (
    id               uuid primary key default gen_random_uuid(),
    user_id          uuid not null references auth.users(id) on delete cascade,
    marker_name      text not null,
    value            numeric,
    unit             text default '',
    reference_range  text default '',
    status           text default 'UNKNOWN',
    date             date,
    source_document  text default '',
    created_at       timestamptz default now()
);

-- ── health_insights ────────────────────────────────────────────────────────────
create table if not exists health_insights (
    id             uuid primary key default gen_random_uuid(),
    user_id        uuid not null references auth.users(id) on delete cascade,
    insights_json  text,
    marker_count   integer default 0,
    created_at     timestamptz default now(),
    unique (user_id)
);

-- ── user_consents ──────────────────────────────────────────────────────────────
create table if not exists user_consents (
    id               uuid primary key default gen_random_uuid(),
    user_id          uuid not null references auth.users(id) on delete cascade,
    consent_type     text not null,
    consent_version  text default 'v2.0',
    ip_address       text,
    user_agent       text,
    is_active        boolean default true,
    granted_at       timestamptz default now(),
    unique (user_id, consent_type)
);

-- ── audit_logs ─────────────────────────────────────────────────────────────────
create table if not exists audit_logs (
    id         uuid primary key default gen_random_uuid(),
    user_id    uuid not null,
    action     text not null,
    detail     text default '',
    category   text default 'GENERAL',
    created_at timestamptz default now()
);

-- ── medical_documents ──────────────────────────────────────────────────────────
create table if not exists medical_documents (
    id                        uuid primary key default gen_random_uuid(),
    user_id                   uuid not null references auth.users(id) on delete cascade,
    filename                  text,
    content                   text,
    doc_type                  text default 'lab_report',
    job_id                    text,
    doctor_prep_text          text,
    doctor_prep_generated_at  timestamptz,
    created_at                timestamptz default now()
);

-- ── conversation_memories ──────────────────────────────────────────────────────
create table if not exists conversation_memories (
    id                   uuid primary key default gen_random_uuid(),
    user_id              uuid not null references auth.users(id) on delete cascade,
    fact                 text not null,
    category             text default 'general',
    source_conversation  uuid,
    is_active            boolean default true,
    created_at           timestamptz default now()
);

-- ── behavioral_logs ────────────────────────────────────────────────────────────
create table if not exists behavioral_logs (
    id          uuid primary key default gen_random_uuid(),
    user_id     uuid not null references auth.users(id) on delete cascade,
    date        date not null,
    metric_name text not null,
    value       numeric not null,
    unit        text default '',
    notes       text default '',
    created_at  timestamptz default now()
);

-- ── documents (RAG vector store) ───────────────────────────────────────────────
create table if not exists documents (
    id         uuid primary key default gen_random_uuid(),
    user_id    uuid references auth.users(id) on delete cascade,
    content    text not null,
    metadata   jsonb default '{}',
    embedding  vector(384),
    created_at timestamptz default now()
);

-- ── Indexes ────────────────────────────────────────────────────────────────────
create index if not exists idx_chats_conv          on chats(conversation_id);
create index if not exists idx_chats_user          on chats(user_id);
create index if not exists idx_convs_user          on conversations(user_id, created_at desc);
create index if not exists idx_markers_user        on health_markers(user_id, date desc);
create index if not exists idx_markers_name        on health_markers(user_id, marker_name);
create index if not exists idx_docs_user           on medical_documents(user_id, created_at desc);
create index if not exists idx_audit_user          on audit_logs(user_id, created_at desc);
create index if not exists idx_memories_user       on conversation_memories(user_id, created_at desc);
create index if not exists idx_behavioral_user     on behavioral_logs(user_id, date desc);
create index if not exists idx_documents_user      on documents(user_id, created_at desc);
create index if not exists idx_documents_embedding on documents using hnsw (embedding vector_cosine_ops);

create unique index if not exists idx_markers_unique
    on health_markers(user_id, marker_name, date);

create unique index if not exists idx_medical_docs_job
    on medical_documents(user_id, job_id)
    where job_id is not null;

-- ── Row Level Security ─────────────────────────────────────────────────────────
alter table user_profiles        enable row level security;
alter table conversations        enable row level security;
alter table chats                enable row level security;
alter table health_markers       enable row level security;
alter table health_insights      enable row level security;
alter table user_consents        enable row level security;
alter table medical_documents    enable row level security;
alter table conversation_memories enable row level security;
alter table behavioral_logs      enable row level security;
alter table documents            enable row level security;

create policy own_profile    on user_profiles         for all using (auth.uid() = user_id);
create policy own_convs      on conversations          for all using (auth.uid() = user_id);
create policy own_chats      on chats                  for all using (auth.uid() = user_id);
create policy own_markers    on health_markers         for all using (auth.uid() = user_id);
create policy own_insights   on health_insights        for all using (auth.uid() = user_id);
create policy own_consents   on user_consents          for all using (auth.uid() = user_id);
create policy own_docs       on medical_documents      for all using (auth.uid() = user_id);
create policy own_memories   on conversation_memories  for all using (auth.uid() = user_id);
create policy own_behavioral on behavioral_logs        for all using (auth.uid() = user_id);
create policy own_documents  on documents              for all using (auth.uid() = user_id);

-- ── match_documents RPC (used by rag.py) ──────────────────────────────────────
create or replace function match_documents(
    query_embedding  vector(384),
    match_threshold  float,
    match_count      int,
    filter_user_id   uuid
)
returns table (
    id         uuid,
    content    text,
    metadata   jsonb,
    similarity float
)
language plpgsql
as $$
begin
    return query
    select
        d.id,
        d.content,
        d.metadata,
        1 - (d.embedding <=> query_embedding) as similarity
    from documents d
    where
        d.user_id = filter_user_id
        and 1 - (d.embedding <=> query_embedding) > match_threshold
    order by d.embedding <=> query_embedding
    limit match_count;
end;
$$;

-- ── decrement_report_credit RPC ───────────────────────────────────────────────
create or replace function decrement_report_credit(uid uuid)
returns void language sql as $$
  update user_profiles 
  set reports_remaining = GREATEST(reports_remaining - 1, 0)
  where user_id = uid and plan = 'free';
$$;