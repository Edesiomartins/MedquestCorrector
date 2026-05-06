"use client";

import { FormEvent, useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import { AlertTriangle, Download, FileUp, Loader2, ScanText } from 'lucide-react';
import { api, visualExamAnalysisApi } from '@/lib/api';

type Grade = {
  score: number | null;
  max_score: number | null;
  verdict: string;
  justification: string;
  needs_human_review: boolean;
  review_reason?: string;
  expected_answer?: string;
};

type QuestionResult = {
  number: number;
  question_number?: number;
  extracted_answer?: string;
  answer_transcription: string;
  reading_confidence: string;
  ocr_confidence?: number | null;
  reading_notes: string;
  image_region?: unknown;
  grade: Grade;
};

type StudentResult = {
  student: { name?: string; registration?: string; class?: string; student_code?: string };
  page: number;
  physical_page?: number;
  detected_student_name?: string;
  detected_registration?: string;
  detected_student_code?: string;
  questions: QuestionResult[];
};

type VisualExamResponse = {
  run_id?: string;
  status: string;
  pdf_name: string;
  pages_processed: number;
  students: StudentResult[];
  warnings: WarningItem[];
};

type WarningItem = {
  code: string;
  message: string;
  detail?: string;
  stage?: string;
  student?: string;
  question?: number;
};

type AnalyzeSuccessResponse = {
  ok: true;
  data: VisualExamResponse;
  warnings?: WarningItem[];
  request_id?: string;
};

type AnalyzeError = {
  message: string;
  detail?: string;
  stage?: string;
  errorCode?: string;
  requestId?: string;
};

type ExamOption = {
  id: string;
  name: string;
  question_count: number;
};

type ExamMode = 'discursive' | 'practical';

const visionModels = [
  'qwen/qwen2.5-vl-72b-instruct',
  'qwen/qwen2.5-vl-32b-instruct',
  'qwen/qwen-2.5-vl-7b-instruct',
  'google/gemini-2.5-flash',
];

const textModels = [
  'openai/gpt-oss-120b',
  'openai/gpt-oss-20b',
  'meta-llama/llama-3.1-8b-instruct',
  'qwen/qwen3-235b-a22b-2507',
];

export default function VisualExamPage() {
  const [file, setFile] = useState<File | null>(null);
  const [examMode, setExamMode] = useState<ExamMode>('discursive');
  const [exams, setExams] = useState<ExamOption[]>([]);
  const [examId, setExamId] = useState('');
  const [loadingExams, setLoadingExams] = useState(false);
  const [visionModel, setVisionModel] = useState(visionModels[0]);
  const [textModel, setTextModel] = useState(textModels[0]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<AnalyzeError | null>(null);
  const [result, setResult] = useState<VisualExamResponse | null>(null);
  const [warnings, setWarnings] = useState<WarningItem[]>([]);
  const [selectedStudentKey, setSelectedStudentKey] = useState('');
  const [exporting, setExporting] = useState(false);
  const [includeDetailsExport, setIncludeDetailsExport] = useState(true);
  const [showErrorDetails, setShowErrorDetails] = useState(false);
  const [editingRowKey, setEditingRowKey] = useState('');
  const [savingReviewKey, setSavingReviewKey] = useState('');
  const [reviewDrafts, setReviewDrafts] = useState<Record<string, { transcription: string; score: string; justification: string; reviewReason: string }>>({});

  useEffect(() => {
    let cancelled = false;
    const loadExams = async () => {
      setLoadingExams(true);
      try {
        const { data } = await api.get<ExamOption[]>('/exams', { params: { practical: examMode === 'practical' } });
        if (cancelled) return;
        setExams(data);
        setExamId(data[0]?.id || '');
      } catch {
        if (cancelled) return;
        setError({ message: 'Não foi possível carregar as provas cadastradas.' });
      } finally {
        if (!cancelled) setLoadingExams(false);
      }
    };
    void loadExams();
    return () => {
      cancelled = true;
    };
  }, [examMode]);

  const studentGroups = useMemo(() => {
    const groups = new Map<string, { label: string; students: StudentResult[] }>();
    for (const item of result?.students || []) {
      const code = (item.detected_student_code || item.student.student_code || '').trim();
      const registration = (item.detected_registration || item.student.registration || '').trim();
      const key = code || registration || `page-${item.physical_page || item.page}`;
      const labelBase = code ? `Aluno ${code}` : (item.detected_student_name || item.student.name || 'Aluno não identificado');
      const label = registration ? `${labelBase} (${registration})` : labelBase;
      if (!groups.has(key)) groups.set(key, { label, students: [] });
      groups.get(key)?.students.push(item);
    }
    return Array.from(groups.entries()).map(([key, value]) => ({ key, ...value }));
  }, [result]);

  const effectiveStudentKey = selectedStudentKey || studentGroups[0]?.key || '';

  const rows = useMemo(() => {
    const group = studentGroups.find((item) => item.key === effectiveStudentKey);
    return (group?.students || []).flatMap((student) => student.questions.map((question) => ({ student, question })));
  }, [studentGroups, effectiveStudentKey]);

  const hasStudentMismatchWarning = useMemo(() => {
    if (!effectiveStudentKey) return false;
    return rows.some(({ student }) => {
      const detectedCode = (student.detected_student_code || student.student.student_code || '').trim();
      const detectedRegistration = (student.detected_registration || student.student.registration || '').trim();
      return detectedCode !== effectiveStudentKey && detectedRegistration !== effectiveStudentKey;
    });
  }, [rows, effectiveStudentKey]);

  const extractApiError = (err: unknown, fallback: string): AnalyzeError => {
    if (!axios.isAxiosError(err)) return { message: fallback };
    const data = err.response?.data as
      | { detail?: unknown; message?: unknown; error_code?: unknown; stage?: unknown; request_id?: unknown }
      | string
      | Blob
      | undefined;
    if (!data || data instanceof Blob) return { message: fallback };
    if (typeof data === 'string') return { message: data || fallback };

    const errorPayload =
      data.detail && typeof data.detail === 'object'
        ? (data.detail as Record<string, unknown>)
        : (data as Record<string, unknown>);

    const message = String(errorPayload.message || data.message || fallback);
    const detail = errorPayload.detail ? String(errorPayload.detail) : undefined;
    const stage = errorPayload.stage ? String(errorPayload.stage) : undefined;
    const errorCode = errorPayload.error_code ? String(errorPayload.error_code) : undefined;
    const requestId = errorPayload.request_id ? String(errorPayload.request_id) : undefined;

    return { message, detail, stage, errorCode, requestId };
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!file) {
      setError({ message: 'Selecione um PDF.' });
      return;
    }
    if (!examId) {
      setError({ message: 'Selecione uma prova para usar o gabarito cadastrado.' });
      return;
    }
    setLoading(true);
    setError(null);
    setWarnings([]);
    setShowErrorDetails(false);
    setResult(null);
    try {
      const body = new FormData();
      body.append('file', file);
      body.append('exam_id', examId);
      if (visionModel) body.append('vision_model', visionModel);
      if (textModel) body.append('text_model', textModel);

      const { data } = await visualExamAnalysisApi.post<AnalyzeSuccessResponse>('/analyze-discursive-pdf', body);
      if (!data?.ok || !data.data || data.data.status !== 'success') {
        setError({ message: 'Falha ao analisar o PDF.' });
        return;
      }
      const mergedWarnings = data.warnings ?? data.data.warnings ?? [];
      setResult({ ...data.data, warnings: mergedWarnings });
      setWarnings(mergedWarnings);
      setSelectedStudentKey('');
    } catch (err: unknown) {
      setError(extractApiError(err, 'Falha ao analisar o PDF.'));
    } finally {
      setLoading(false);
    }
  };

  const handleExamModeChange = (mode: ExamMode) => {
    setExamMode(mode);
    setResult(null);
    setWarnings([]);
    setError(null);
    setSelectedStudentKey('');
  };

  const handleExportSpreadsheet = async () => {
    if (!result?.run_id) return;
    setExporting(true);
    setError(null);
    try {
      const response = await visualExamAnalysisApi.get(`/runs/${result.run_id}/export`, {
        params: { include_details: includeDetailsExport },
        responseType: 'blob',
      });
      const blobUrl = window.URL.createObjectURL(new Blob([response.data]));
      const a = document.createElement('a');
      a.href = blobUrl;
      a.setAttribute('download', `notas_manuscrita_${result.run_id}.xlsx`);
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(blobUrl);
    } catch (err: unknown) {
      setError(extractApiError(err, 'Falha ao exportar planilha.'));
    } finally {
      setExporting(false);
    }
  };

  const rowKey = (student: StudentResult, question: QuestionResult) =>
    `${student.physical_page || student.page}-${question.question_number || question.number}`;

  const beginReview = (student: StudentResult, question: QuestionResult) => {
    const key = rowKey(student, question);
    setReviewDrafts((prev) => ({
      ...prev,
      [key]: prev[key] || {
        transcription: question.extracted_answer || question.answer_transcription || '',
        score: question.grade?.score != null ? String(question.grade.score) : '',
        justification: question.grade?.justification || question.reading_notes || '',
        reviewReason: question.grade?.review_reason || '',
      },
    }));
    setEditingRowKey(key);
  };

  const updateDraft = (key: string, field: keyof (typeof reviewDrafts)[string], value: string) => {
    setReviewDrafts((prev) => ({
      ...prev,
      [key]: { ...prev[key], [field]: value },
    }));
  };

  const applyLocalReview = (student: StudentResult, question: QuestionResult, draft: (typeof reviewDrafts)[string]) => {
    const pageNumber = student.physical_page || student.page;
    const questionNumber = question.question_number || question.number;
    const numericScore = draft.score.trim() === '' ? null : Number(draft.score);
    setResult((prev) => {
      if (!prev) return prev;
      return {
        ...prev,
        students: prev.students.map((item) => {
          if ((item.physical_page || item.page) !== pageNumber) return item;
          return {
            ...item,
            questions: item.questions.map((q) => {
              if ((q.question_number || q.number) !== questionNumber) return q;
              return {
                ...q,
                extracted_answer: draft.transcription,
                answer_transcription: draft.transcription,
                grade: {
                  ...q.grade,
                  score: Number.isFinite(numericScore) ? numericScore : null,
                  justification: draft.justification,
                  needs_human_review: false,
                  review_reason: draft.reviewReason,
                },
              };
            }),
          };
        }),
      };
    });
  };

  const saveReview = async (student: StudentResult, question: QuestionResult) => {
    if (!result?.run_id) return;
    const key = rowKey(student, question);
    const draft = reviewDrafts[key];
    if (!draft) return;
    const pageNumber = student.physical_page || student.page;
    const questionNumber = question.question_number || question.number;
    const numericScore = draft.score.trim() === '' ? null : Number(draft.score);

    setSavingReviewKey(key);
    setError(null);
    try {
      await visualExamAnalysisApi.patch(`/runs/${result.run_id}/answers`, {
        page_number: pageNumber,
        question_number: questionNumber,
        answer_transcription: draft.transcription,
        score: Number.isFinite(numericScore) ? numericScore : null,
        verdict: question.grade?.verdict || '',
        justification: draft.justification,
        needs_human_review: false,
        review_reason: draft.reviewReason || null,
      });
      applyLocalReview(student, question, draft);
      setEditingRowKey('');
    } catch (err: unknown) {
      setError(extractApiError(err, 'Falha ao salvar revisão.'));
    } finally {
      setSavingReviewKey('');
    }
  };

  return (
    <div className="space-y-6 animate-in fade-in slide-in-from-bottom-4 duration-500">
      <div className="flex items-center justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Correção Visual de Provas</h1>
          <p className="text-slate-500 mt-1">Leitura visual com correção pelo módulo discursivo ou prático da prova selecionada.</p>
        </div>
        <ScanText className="w-9 h-9 text-emerald-600" />
      </div>

      <form onSubmit={submit} className="glass-panel rounded-xl p-6 space-y-5">
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <label className="space-y-2">
            <span className="text-sm font-medium">PDF escaneado</span>
            <input
              type="file"
              accept="application/pdf"
              onChange={(event) => setFile(event.target.files?.[0] || null)}
              className="block w-full text-sm file:mr-4 file:rounded-lg file:border-0 file:bg-emerald-50 file:px-4 file:py-2 file:font-medium file:text-emerald-700 hover:file:bg-emerald-100"
            />
          </label>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="space-y-2 md:col-span-2">
              <span className="text-sm font-medium">Tipo de prova</span>
              <div className="inline-flex rounded-lg border border-surface-border bg-surface p-1">
                <button
                  type="button"
                  onClick={() => handleExamModeChange('discursive')}
                  className={`rounded-md px-3 py-1.5 text-sm font-medium ${examMode === 'discursive' ? 'bg-emerald-600 text-white' : 'text-slate-600'}`}
                >
                  Discursiva
                </button>
                <button
                  type="button"
                  onClick={() => handleExamModeChange('practical')}
                  className={`rounded-md px-3 py-1.5 text-sm font-medium ${examMode === 'practical' ? 'bg-emerald-600 text-white' : 'text-slate-600'}`}
                >
                  Prática
                </button>
              </div>
            </div>
            <label className="space-y-2 md:col-span-2">
              <span className="text-sm font-medium">
                {examMode === 'practical' ? 'Prova prática' : 'Prova discursiva'} (usa respostas esperadas cadastradas)
              </span>
              <select
                value={examId}
                onChange={(event) => setExamId(event.target.value)}
                disabled={loadingExams || exams.length === 0}
                className="w-full rounded-lg border border-surface-border bg-surface px-3 py-2 text-sm"
              >
                {exams.length === 0 ? (
                  <option value="">Nenhuma prova {examMode === 'practical' ? 'prática' : 'discursiva'} cadastrada</option>
                ) : (
                  exams.map((exam) => (
                    <option key={exam.id} value={exam.id}>
                      {exam.name} ({exam.question_count} questões)
                    </option>
                  ))
                )}
              </select>
            </label>
            <label className="space-y-2">
              <span className="text-sm font-medium">Modelo de visão</span>
              <select value={visionModel} onChange={(event) => setVisionModel(event.target.value)} className="w-full rounded-lg border border-surface-border bg-surface px-3 py-2 text-sm">
                {visionModels.map((model) => <option key={model} value={model}>{model}</option>)}
              </select>
            </label>
            <label className="space-y-2">
              <span className="text-sm font-medium">Modelo textual</span>
              <select
                value={textModel}
                onChange={(event) => setTextModel(event.target.value)}
                disabled={examMode === 'practical'}
                className="w-full rounded-lg border border-surface-border bg-surface px-3 py-2 text-sm disabled:opacity-60"
              >
                {textModels.map((model) => <option key={model} value={model}>{model}</option>)}
              </select>
            </label>
          </div>
        </div>

        {error && (
          <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
            <div className="flex items-center gap-2">
              <AlertTriangle className="w-4 h-4" />
              <span>{error.message}</span>
            </div>
            {(error.stage || error.errorCode || error.requestId) ? (
              <div className="mt-2 text-xs text-red-800/80">
                {error.stage ? <div>Etapa: {error.stage}</div> : null}
                {error.errorCode ? <div>Código: {error.errorCode}</div> : null}
                {error.requestId ? <div>ID do erro: {error.requestId}</div> : null}
              </div>
            ) : null}
            {error.detail ? (
              <div className="mt-2">
                <button
                  type="button"
                  onClick={() => setShowErrorDetails((v) => !v)}
                  className="text-xs underline"
                >
                  {showErrorDetails ? 'Ocultar detalhes técnicos' : 'Ver detalhes técnicos'}
                </button>
                {showErrorDetails ? (
                  <div className="mt-1 rounded border border-red-200 bg-red-100/60 px-2 py-1 text-xs">
                    {error.detail}
                  </div>
                ) : null}
              </div>
            ) : null}
          </div>
        )}

        {warnings.length > 0 && !error && (
          <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
            Análise concluída com avisos. Algumas questões precisam de revisão.
          </div>
        )}

        <button type="submit" disabled={loading} className="btn-primary inline-flex items-center gap-2 disabled:opacity-60">
          {loading ? <Loader2 className="w-5 h-5 animate-spin" /> : <FileUp className="w-5 h-5" />}
          <span>{examMode === 'practical' ? 'Analisar prova prática' : 'Analisar prova discursiva'}</span>
        </button>
      </form>

      {result && (
        <div className="glass-panel w-full min-w-0 rounded-xl overflow-hidden">
          <div className="p-5 border-b border-surface-border flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
            <div className="min-w-0">
              <h2 className="text-lg font-bold">{result.pdf_name}</h2>
              <p className="text-sm text-slate-500">{result.pages_processed} página(s) processada(s)</p>
              <label className="mt-2 inline-flex items-center gap-2 text-xs text-slate-600">
                <input
                  type="checkbox"
                  checked={includeDetailsExport}
                  onChange={(event) => setIncludeDetailsExport(event.target.checked)}
                />
                Incluir detalhamento por questão
              </label>
            </div>
            <button
              type="button"
              onClick={handleExportSpreadsheet}
              disabled={!result.run_id || exporting}
              className="btn-primary inline-flex h-fit items-center gap-2 disabled:cursor-not-allowed disabled:opacity-60"
              title={result.run_id ? 'Baixar planilha XLSX com os resultados da tabela' : 'Execução sem ID para exportação'}
            >
              {exporting ? <Loader2 className="w-4 h-4 animate-spin" /> : <Download className="w-4 h-4" />}
              <span>Exportar XLSX</span>
            </button>
            {warnings.length > 0 && (
              <div className="text-sm text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
                {warnings[0]?.message || warnings[0]?.detail || 'Aviso no processamento.'}
              </div>
            )}
          </div>
          <div className="px-5 py-4 border-b border-surface-border flex flex-col gap-3 sm:flex-row sm:items-center sm:gap-4">
            <label className="text-sm font-medium">Aluno detectado</label>
            <select
              value={effectiveStudentKey}
              onChange={(event) => setSelectedStudentKey(event.target.value)}
              className="w-full min-w-0 rounded-lg border border-surface-border bg-surface px-3 py-2 text-sm sm:w-auto sm:min-w-72"
            >
              {studentGroups.map((group) => (
                <option key={group.key} value={group.key}>
                  {group.label}
                </option>
              ))}
            </select>
          </div>
          {hasStudentMismatchWarning && (
            <div className="mx-5 mt-4 flex items-center gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
              <AlertTriangle className="w-4 h-4" />
              <span>Possível divergência de vinculação aluno-página</span>
            </div>
          )}

          <div className="w-full min-w-0 overflow-x-auto overscroll-x-contain">
            <table className="min-w-[1720px] table-fixed border-collapse text-left text-sm">
              <thead>
                <tr className="bg-slate-50/70 dark:bg-slate-800/40 text-slate-500 uppercase text-xs">
                  <th className="px-4 py-3">Aluno</th>
                  <th className="px-4 py-3">Matrícula</th>
                  <th className="px-4 py-3">Turma</th>
                  <th className="px-4 py-3">Página física</th>
                  <th className="px-4 py-3">Questão</th>
                  <th className="px-4 py-3 min-w-80">Transcrição</th>
                  <th className="px-4 py-3">Confiança</th>
                  <th className="px-4 py-3">OCR conf.</th>
                  <th className="px-4 py-3">Nota</th>
                  <th className="px-4 py-3">Veredito</th>
                  <th className="px-4 py-3 min-w-72">Padrão</th>
                  <th className="px-4 py-3 min-w-72">Justificativa</th>
                  <th className="px-4 py-3">Revisão</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-surface-border">
                {rows.map(({ student, question }) => {
                  const lowReading = question.reading_confidence === 'baixa';
                  const zeroScore = question.grade?.score === 0;
                  const review = question.grade?.needs_human_review || lowReading;
                  const key = rowKey(student, question);
                  const editing = editingRowKey === key;
                  const draft = reviewDrafts[key];
                  return (
                    <tr key={key} className={review ? 'bg-amber-50/60 dark:bg-amber-500/10' : ''}>
                      <td className="px-4 py-3 font-medium">{student.detected_student_name || student.student.name || 'Não identificado'}</td>
                      <td className="px-4 py-3">{student.detected_registration || student.student.registration || '-'}</td>
                      <td className="px-4 py-3">{student.student.class || '-'}</td>
                      <td className="px-4 py-3">{student.physical_page || student.page}</td>
                      <td className="px-4 py-3">{question.question_number || question.number}</td>
                      <td className="px-4 py-3 whitespace-pre-wrap">
                        {editing && draft ? (
                          <textarea
                            value={draft.transcription}
                            onChange={(event) => updateDraft(key, 'transcription', event.target.value)}
                            className="min-h-24 w-full rounded-lg border border-surface-border bg-white px-3 py-2 text-sm"
                          />
                        ) : (
                          question.extracted_answer || question.answer_transcription || '[sem resposta]'
                        )}
                      </td>
                      <td className="px-4 py-3">
                        <span className={`rounded px-2 py-1 text-xs font-medium ${lowReading ? 'bg-red-100 text-red-700' : 'bg-emerald-100 text-emerald-700'}`}>
                          {question.reading_confidence}
                        </span>
                      </td>
                      <td className="px-4 py-3">{question.ocr_confidence != null ? `${Math.round(question.ocr_confidence * 100)}%` : '-'}</td>
                      <td className={`px-4 py-3 font-semibold ${zeroScore ? 'text-red-700' : ''}`}>
                        {editing && draft ? (
                          <input
                            type="number"
                            min={0}
                            max={question.grade?.max_score ?? undefined}
                            step={0.25}
                            value={draft.score}
                            onChange={(event) => updateDraft(key, 'score', event.target.value)}
                            className="w-24 rounded-lg border border-surface-border bg-white px-3 py-2 text-sm"
                          />
                        ) : (
                          <>{question.grade?.score ?? '-'} / {question.grade?.max_score ?? '-'}</>
                        )}
                      </td>
                      <td className="px-4 py-3">{question.grade?.verdict || '-'}</td>
                      <td className="px-4 py-3 whitespace-pre-wrap">{question.grade?.expected_answer || '-'}</td>
                      <td className="px-4 py-3">
                        {editing && draft ? (
                          <textarea
                            value={draft.justification}
                            onChange={(event) => updateDraft(key, 'justification', event.target.value)}
                            className="min-h-24 w-full rounded-lg border border-surface-border bg-white px-3 py-2 text-sm"
                          />
                        ) : (
                          question.grade?.justification || question.reading_notes || '-'
                        )}
                      </td>
                      <td className="px-4 py-3">
                        <div className="mb-2 text-xs font-medium">{review ? 'sim' : 'não'}</div>
                        {editing && draft ? (
                          <div className="flex min-w-52 flex-col gap-2">
                            <textarea
                              value={draft.reviewReason}
                              onChange={(event) => updateDraft(key, 'reviewReason', event.target.value)}
                              placeholder="Motivo/observação da revisão"
                              className="min-h-20 rounded-lg border border-surface-border bg-white px-3 py-2 text-xs"
                            />
                            <button
                              type="button"
                              onClick={() => void saveReview(student, question)}
                              disabled={savingReviewKey === key}
                              className="rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-60"
                            >
                              {savingReviewKey === key ? 'salvando...' : 'salvar correção final'}
                            </button>
                            <button type="button" onClick={() => setEditingRowKey('')} className="text-xs text-slate-600 underline">
                              cancelar
                            </button>
                          </div>
                        ) : (
                          <button
                            type="button"
                            onClick={() => beginReview(student, question)}
                            className={`rounded-lg px-3 py-1.5 text-xs font-medium ${review ? 'bg-amber-100 text-amber-800' : 'bg-slate-100 text-slate-600'}`}
                          >
                            revisar manualmente
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
