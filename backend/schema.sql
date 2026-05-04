CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Enums
CREATE TYPE role_enum AS ENUM ('PROFESSOR', 'ADMIN');
CREATE TYPE batch_status AS ENUM ('PENDING', 'PROCESSING', 'REVIEW_PENDING', 'DONE', 'FAILED');
CREATE TYPE result_status AS ENUM ('PENDING', 'GRADED', 'AUTO_APPROVED', 'REVIEWED');

-- 1. Users
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    email VARCHAR NOT NULL UNIQUE,
    password_hash VARCHAR NOT NULL,
    role role_enum NOT NULL DEFAULT 'PROFESSOR',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_users_email ON users(email);

-- 2. Classes & Students
CREATE TABLE classes (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name VARCHAR NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE students (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    class_id UUID NOT NULL REFERENCES classes(id),
    name VARCHAR NOT NULL,
    registration_number VARCHAR NOT NULL,
    curso VARCHAR,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 3. Exams & Questions
CREATE TABLE exams (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    class_id UUID REFERENCES classes(id),
    name VARCHAR NOT NULL,
    is_practical BOOLEAN NOT NULL DEFAULT FALSE,
    layout_manifest_json TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE exam_questions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    exam_id UUID NOT NULL REFERENCES exams(id),
    question_number INTEGER NOT NULL,
    question_text TEXT NOT NULL,
    expected_answer TEXT NOT NULL,
    correction_criteria TEXT,
    max_score FLOAT NOT NULL DEFAULT 1.0,
    page_number INTEGER,
    box_x FLOAT,
    box_y FLOAT,
    box_w FLOAT,
    box_h FLOAT
);

-- 4. Upload Batches
CREATE TABLE upload_batches (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    exam_id UUID NOT NULL REFERENCES exams(id),
    file_url VARCHAR NOT NULL,
    status batch_status NOT NULL DEFAULT 'PENDING',
    total_pages INTEGER DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 5. Results & Scores
CREATE TABLE student_results (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    batch_id UUID NOT NULL REFERENCES upload_batches(id),
    student_id UUID REFERENCES students(id),
    identity_source VARCHAR(40),
    physical_page INTEGER,
    detected_student_name VARCHAR(255),
    detected_registration VARCHAR(100),
    warnings_json JSONB DEFAULT '[]'::jsonb,
    page_number INTEGER NOT NULL,
    total_score FLOAT DEFAULT 0.0,
    status result_status DEFAULT 'PENDING',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE question_scores (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    student_result_id UUID NOT NULL REFERENCES student_results(id),
    question_id UUID NOT NULL REFERENCES exam_questions(id),
    ai_score FLOAT DEFAULT 0.0,
    ai_justification TEXT,
    final_score FLOAT,
    professor_comment TEXT,
    extracted_answer_text TEXT,
    ocr_provider VARCHAR,
    ocr_confidence FLOAT,
    grading_confidence FLOAT,
    requires_manual_review BOOLEAN NOT NULL DEFAULT FALSE,
    manual_review_reason TEXT,
    criteria_met_json TEXT,
    criteria_missing_json TEXT,
    source_page_number INTEGER,
    source_question_number INTEGER,
    crop_box_json TEXT,
    answer_crop_path TEXT,
    transcription_confidence FLOAT,
    warnings_json JSONB DEFAULT '[]'::jsonb
);
