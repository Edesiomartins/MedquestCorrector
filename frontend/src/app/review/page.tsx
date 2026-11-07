"use client";

import { useEffect, useState } from 'react';
import { AlertCircle, CheckCircle, Loader2 } from 'lucide-react';
import Link from 'next/link';
import { api } from '@/lib/api';
import { AnswerCrop } from '@/components/AnswerCrop';

type QuestionScoreDetail = {
  id: string;
  question_number: number;
  question_text: string;
  max_score: number;
  ai_score: number;
  ai_justification: string | null;
  final_score: number | null;
  professor_comment: string | null;
  extracted_answer_text: string | null;
  ocr_provider: string | null;
  ocr_confidence: number | null;
  grading_confidence: number | null;
  requires_manual_review: boolean;
  manual_review_reason: string | null;
  source_page_number: number | null;
  answer_crop_path?: string | null;
  transcription_edited_by_human?: boolean;
  transcription_confidence?: number | null;
  warnings_json?: string[] | null;
};

type StudentResultDetail = {
  id: string;
  batch_id: string;
  student_name: string | null;
  registration_number: string | null;
  page_number: number;
  physical_page?: number | null;
  identity_source?: string | null;
  detected_student_name?: string | null;
  detected_registration?: string | null;
  handwritten_name?: string | null;
  confirmed_name?: string | null;
  warnings_json?: string[] | null;
  total_score: number;
  status: string;
  scores: QuestionScoreDetail[];
};

type ReviewError = {
  message: string;
  detail?: string;
  stage?: string;
  errorCode?: string;
  requestId?: string;
};

function shouldDisplayAiJustification(value: string | null): boolean {
  if (!value) return false;
  const normalized = value.trim().toLowerCase();
  if (!normalized) return false;
  if (normalized.startsWith('erro no processamento:')) return false;
  return true;
}

export default function ReviewPage() {
  const [result, setResult] = useState<StudentResultDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ReviewError | null>(null);
  const [showErrorDetails, setShowErrorDetails] = useState(false);
  const [saving, setSaving] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [includeDetailsExport, setIncludeDetailsExport] = useState(true);
  const [lastBatchId, setLastBatchId] = useState<string | null>(null);
  // `transcription` guarda a leitura conferida pelo professor. Corrigi-la ali
  // fecha o ciclo de rotulagem: cada edicao vira um par
  // (recorte, leitura do modelo, leitura humana) no backend.
  const [localScores, setLocalScores] = useState<
    Record<string, { score: number; comment: string; transcription: string }>
  >({});
  const [gradeConfirmed, setGradeConfirmed] = useState<Record<string, boolean>>({});

  const fetchNext = async () => {
    setLoading(true);
    setError(null);
    try {
      const { data } = await api.get<StudentResultDetail>('/reviews/next');
      setResult(data);
      setLastBatchId(data.batch_id);
      const initial: Record<string, { score: number; comment: string; transcription: string }> = {};
      for (const s of data.scores) {
        if (!s.requires_manual_review) continue;
        initial[s.id] = {
          score: s.final_score ?? s.ai_score,
          comment: s.professor_comment ?? "",
          transcription: s.extracted_answer_text ?? "",
        };
      }
      setLocalScores(initial);
      setGradeConfirmed({});
    } catch (err: unknown) {
      const statusCode = (
        err &&
        typeof err === 'object' &&
        'response' in err &&
        (err as { response?: { status?: number } }).response?.status
      ) || undefined;
      if (statusCode === 404) {
        const payload = (
          err &&
          typeof err === 'object' &&
          'response' in err &&
          (err as { response?: { data?: { detail?: Record<string, unknown> } } }).response?.data?.detail
        ) as Record<string, unknown> | undefined;
        if (payload?.latest_batch_id) {
          setLastBatchId(String(payload.latest_batch_id));
        }
        setError({
          message: String(payload?.message || "Nenhuma correção pendente de revisão."),
          detail: payload?.detail ? String(payload.detail) : undefined,
          stage: payload?.stage ? String(payload.stage) : undefined,
          errorCode: payload?.error_code ? String(payload.error_code) : undefined,
          requestId: payload?.request_id ? String(payload.request_id) : undefined,
        });
      } else {
        const payload = (
          err &&
          typeof err === 'object' &&
          'response' in err &&
          (err as { response?: { data?: { detail?: Record<string, unknown> } } }).response?.data?.detail
        ) as Record<string, unknown> | undefined;
        setError({
          message: String(payload?.message || "Erro ao carregar revisão."),
          detail: payload?.detail ? String(payload.detail) : undefined,
          stage: payload?.stage ? String(payload.stage) : undefined,
          errorCode: payload?.error_code ? String(payload.error_code) : undefined,
          requestId: payload?.request_id ? String(payload.request_id) : undefined,
        });
      }
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void fetchNext();
    }, 0);
    return () => window.clearTimeout(timer);
  }, []);

  const handleApprove = async () => {
    if (!result) return;
    const pending = result.scores.filter((s) => s.requires_manual_review);
    if (pending.length === 0) return;

    setSaving(true);

    try {
      for (const s of pending) {
        const local = localScores[s.id];
        if (local) {
          await api.post(`/reviews/scores/${s.id}`, {
            final_score: local.score,
            professor_comment: local.comment || null,
            extracted_answer_text: local.transcription ?? "",
            grade_confirmed: gradeConfirmed[s.id] === true || local.transcription === (s.extracted_answer_text ?? ""),
          });
        }
      }
      await api.post(`/reviews/results/${result.id}/approve`);
      await fetchNext();
    } catch {
      setError({ message: "Erro ao salvar revisão." });
    } finally {
      setSaving(false);
    }
  };

  const updateLocal = (
    scoreId: string,
    field: "score" | "comment" | "transcription",
    value: number | string,
  ) => {
    setLocalScores((prev) => ({
      ...prev,
      [scoreId]: { ...prev[scoreId], [field]: value },
    }));
    if (field === "transcription") {
      setGradeConfirmed((prev) => ({ ...prev, [scoreId]: false }));
    }
    if (field === "score") {
      setGradeConfirmed((prev) => ({ ...prev, [scoreId]: true }));
    }
  };

  const handleExportBatch = async (batchId = result?.batch_id) => {
    if (!batchId) return;
    setExporting(true);
    try {
      const response = await api.get(`/reviews/batch/${batchId}/export`, {
        params: { include_details: includeDetailsExport },
        responseType: "blob",
      });
      const blobUrl = window.URL.createObjectURL(new Blob([response.data]));
      const a = document.createElement("a");
      a.href = blobUrl;
      a.setAttribute("download", `revisao_lote_${batchId}.xlsx`);
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(blobUrl);
    } catch {
      setError({ message: "Erro ao exportar planilha da revisão." });
    } finally {
      setExporting(false);
    }
  };

  if (loading) {
    return (
      <div className="h-[calc(100vh-8rem)] flex items-center justify-center">
        <Loader2 className="w-8 h-8 animate-spin text-emerald-600" />
      </div>
    );
  }

  if (error || !result) {
    const queueEmpty = Boolean(error?.message?.includes("Nenhuma correção"));
    const technicalError = Boolean(
      error?.message?.startsWith("Erro ao carregar") || error?.message?.startsWith("Erro ao salvar"),
    );

    return (
      <div className="h-[calc(100vh-8rem)] flex flex-col items-center justify-center space-y-4">
        <div
          className={
            technicalError
              ? "bg-amber-50 text-amber-950 p-6 rounded-xl border border-amber-200 flex flex-col items-center text-center max-w-md"
              : "bg-emerald-50 text-emerald-700 p-6 rounded-xl border border-emerald-200 flex flex-col items-center text-center max-w-md"
          }
        >
          {technicalError ? (
            <AlertCircle className="w-12 h-12 mb-4 text-amber-600 shrink-0" aria-hidden />
          ) : (
            <CheckCircle className="w-12 h-12 mb-4 shrink-0" aria-hidden />
          )}
          <h2 className="text-xl font-bold">{error?.message || "Tudo pronto!"}</h2>
          {(error?.stage || error?.errorCode || error?.requestId) ? (
            <div className="mt-2 text-xs">
              {error?.stage ? <div>Etapa: {error.stage}</div> : null}
              {error?.errorCode ? <div>Código: {error.errorCode}</div> : null}
              {error?.requestId ? <div>ID do erro: {error.requestId}</div> : null}
            </div>
          ) : null}
          {error?.detail ? (
            <div className="mt-2">
              <button
                type="button"
                onClick={() => setShowErrorDetails((v) => !v)}
                className="text-xs underline"
              >
                {showErrorDetails ? "Ocultar detalhes técnicos" : "Ver detalhes técnicos"}
              </button>
              {showErrorDetails ? <div className="mt-1 text-xs">{error.detail}</div> : null}
            </div>
          ) : null}
          <p className="mt-2 text-sm opacity-90">
            {queueEmpty
              ? "Você está em dia com as correções."
              : technicalError
                ? "Não foi possível comunicar com o servidor (rede, sessão ou API). Atualize a página ou entre novamente."
                : ""}
          </p>
        </div>
        <div className="flex flex-wrap gap-3 justify-center">
          {queueEmpty && lastBatchId ? (
            <>
              <label className="inline-flex items-center gap-2 text-sm text-slate-700">
                <input
                  type="checkbox"
                  checked={includeDetailsExport}
                  onChange={(e) => setIncludeDetailsExport(e.target.checked)}
                />
                Incluir detalhamento por questão
              </label>
              <button
                type="button"
                className="btn-primary"
                onClick={() => void handleExportBatch(lastBatchId)}
                disabled={exporting}
              >
                {exporting ? "Exportando..." : "Exportar Excel do último lote"}
              </button>
            </>
          ) : null}
          {technicalError ? (
            <button type="button" className="btn-primary" onClick={() => void fetchNext()}>
              Tentar novamente
            </button>
          ) : null}
          <Link href="/">
            <button type="button" className={technicalError ? "btn-secondary" : "btn-primary"}>
              Voltar para a Home
            </button>
          </Link>
        </div>
      </div>
    );
  }

  const pendingScores = result.scores.filter((s) => s.requires_manual_review);
  const autoScores = result.scores.filter((s) => !s.requires_manual_review);
  const sourcePages = [
    ...new Set(
      result.scores
        .map((s) => s.source_page_number)
        .filter((n): n is number => n != null && Number.isFinite(n)),
    ),
  ].sort((a, b) => a - b);
  const identityLabel: Record<string, string> = {
    qr: "QR Code na folha",
    header_ocr: "Cabeçalho (OCR)",
    manifest_fallback: "Manifesto (fallback — risco se PDF fora de ordem)",
    anonymous: "Anônimo (sem identificação confiável)",
  };

  return (
    <div className="max-w-4xl mx-auto space-y-6 animate-in fade-in duration-500">
      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Revisão de exceções</h1>
          <p className="text-slate-500 text-sm mt-1">
            Corrija apenas itens duvidosos; questões autoaprovadas ficam como referência abaixo.
          </p>
          <p className="text-slate-600 text-sm mt-1">
            {result.student_name
              ? `${result.student_name} (${result.registration_number})`
              : `Primeira página lógica do lote: ${result.page_number}`}
            {" — Total: "}
            <span className="font-bold text-emerald-600">{result.total_score.toFixed(2)} pts</span>
          </p>
          <p className="text-slate-500 text-xs mt-1.5 space-y-0.5">
            {result.identity_source && result.identity_source !== "qr" ? (
              <span className="block rounded border border-amber-300 bg-amber-50 px-2 py-1 text-amber-800">
                Vinculação feita sem QR confiável. Conferir aluno manualmente.
              </span>
            ) : null}
            {result.identity_source ? (
              <span className="block">
                <span className="text-slate-400">Vínculo aluno: </span>
                <span className="font-medium text-slate-600">
                  {result.identity_source} — {identityLabel[result.identity_source] ?? result.identity_source}
                </span>
              </span>
            ) : null}
            {sourcePages.length > 0 ? (
              <span className="block">
                <span className="text-slate-400">Páginas físicas (questões): </span>
                {sourcePages.join(", ")}
              </span>
            ) : null}
            <span className="block text-slate-400">
              Página lógica do resultado (ordem no lote): {result.page_number}
            </span>
            {result.physical_page != null ? (
              <span className="block text-slate-400">
                Página física principal: {result.physical_page}
              </span>
            ) : null}
            {result.detected_student_name ? (
              <span className="block">
                Nome manuscrito: {result.detected_student_name}
              </span>
            ) : null}
            {result.confirmed_name ? (
              <span className="block">Nome confirmado: {result.confirmed_name}</span>
            ) : null}
          </p>
        </div>
        <button
          onClick={handleApprove}
          disabled={saving || pendingScores.length === 0}
          className="btn-primary flex items-center space-x-2 shadow-emerald-500/20 shadow-md disabled:opacity-50"
        >
          {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <CheckCircle className="w-4 h-4" />}
          <span>Confirmar pendências & próximo</span>
        </button>
      </div>

      <div className="flex items-center justify-between rounded-xl border border-slate-200 bg-slate-50/60 p-3">
        <label className="inline-flex items-center gap-2 text-sm text-slate-700">
          <input
            type="checkbox"
            checked={includeDetailsExport}
            onChange={(e) => setIncludeDetailsExport(e.target.checked)}
          />
          Incluir detalhamento por questão
        </label>
        <button
          type="button"
          onClick={() => void handleExportBatch()}
          disabled={exporting || !result?.batch_id}
          className="btn-secondary disabled:opacity-60"
        >
          {exporting ? "Exportando..." : "Exportar XLSX"}
        </button>
      </div>

      {pendingScores.length === 0 && (
        <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-amber-900 text-sm">
          Nenhuma questão pendente neste resultado. Use o botão para finalizar ou avance com a fila.
        </div>
      )}

      {pendingScores.map((s) => {
        const local = localScores[s.id] || {
          score: s.final_score ?? s.ai_score,
          comment: s.professor_comment ?? "",
          transcription: s.extracted_answer_text ?? "",
        };
        return (
          <div key={s.id} className="glass-panel rounded-xl p-6 border border-surface-border space-y-4">
            <div className="flex justify-between items-start gap-4">
              <div>
                <h3 className="font-bold text-lg">Questão {s.question_number}</h3>
                {s.source_page_number != null && (
                  <p className="text-xs text-slate-400 mt-0.5">Página física: {s.source_page_number}</p>
                )}
                <p className="text-sm text-slate-500 mt-1">{s.question_text}</p>
              </div>
              <div className="text-right shrink-0">
                <div className="flex items-center gap-2">
                  <span className="text-xs text-slate-400">IA:</span>
                  <span className="text-lg font-bold text-emerald-600">{s.ai_score.toFixed(2)}</span>
                  <span className="text-slate-400">/ {s.max_score.toFixed(2)}</span>
                </div>
              </div>
            </div>

            {s.manual_review_reason && (
              <div className="bg-amber-50 dark:bg-amber-950/30 p-3 rounded-lg text-sm text-amber-900 dark:text-amber-200 border border-amber-200/80">
                <span className="font-semibold text-xs uppercase">Motivo da revisão: </span>
                {s.manual_review_reason}
              </div>
            )}

            {s.transcription_confidence != null && s.transcription_confidence < 0.7 ? (
              <div className="bg-amber-50 p-3 rounded-lg text-sm text-amber-900 border border-amber-200/80">
                Transcrição com baixa confiança. Conferir imagem.
              </div>
            ) : null}

            {s.warnings_json && s.warnings_json.length > 0 ? (
              <div className="bg-slate-50 p-3 rounded-lg text-xs text-slate-700 border border-slate-200">
                Alertas: {s.warnings_json.join(" | ")}
              </div>
            ) : null}

            <div className="grid grid-cols-2 gap-3 text-xs text-slate-600 dark:text-slate-400">
              <div>
                Confiança OCR:{" "}
                <span className="font-medium">
                  {s.ocr_confidence != null ? `${(s.ocr_confidence * 100).toFixed(0)}%` : "—"}
                </span>
                {s.ocr_provider ? ` (${s.ocr_provider})` : ""}
              </div>
              <div>
                Confiança IA:{" "}
                <span className="font-medium">
                  {s.grading_confidence != null ? `${(s.grading_confidence * 100).toFixed(0)}%` : "—"}
                </span>
              </div>
            </div>

            {/* Recorte à esquerda, transcrição à direita: é a comparação que o
                professor precisa fazer, e fazê-la lado a lado leva segundos. */}
            {/* Recorte à esquerda, transcrição editável à direita: é a
                comparação que o professor precisa fazer, e cada correção aqui
                vira dado rotulado para melhorar a leitura. */}
            <div className="grid gap-3 md:grid-cols-2">
              <AnswerCrop scoreId={s.id} />
              <div className="space-y-1">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-semibold uppercase text-slate-500">
                    Transcrição
                  </span>
                  {local.transcription !== (s.extracted_answer_text ?? "") ? (
                    <span className="text-xs text-amber-700">
                      transcrição alterada — confirme a nota de novo
                    </span>
                  ) : null}
                </div>
                <textarea
                  value={local.transcription}
                  onChange={(e) => updateLocal(s.id, "transcription", e.target.value)}
                  rows={5}
                  placeholder="Nenhum texto transcrito. Se a caixa estiver em branco, deixe vazio."
                  className="w-full rounded-lg border border-slate-200 bg-slate-50 p-3 text-sm text-slate-700 focus:border-emerald-500 focus:outline-none focus:ring-2 focus:ring-emerald-500/20 dark:border-slate-700 dark:bg-slate-800/50 dark:text-slate-300"
                />
                <p className="text-xs text-slate-400">
                  Escreva o que está no papel, sem corrigir português nem completar palavras.
                </p>
              </div>
            </div>

            {shouldDisplayAiJustification(s.ai_justification) && (
              <div className="bg-slate-50 dark:bg-slate-800/50 p-3 rounded-lg text-sm text-slate-600 dark:text-slate-400">
                <span className="font-semibold text-xs text-slate-500 uppercase">Justificativa IA: </span>
                {s.ai_justification}
              </div>
            )}

            <div className="flex items-center gap-4">
              <label className="text-sm font-medium text-slate-600">Nota final:</label>
              <div className="flex items-center gap-1">
                {[0, 0.25, 0.5, 0.75, 1].map((v) => (
                  <button
                    key={`${s.id}-${v}`}
                    type="button"
                    onClick={() => updateLocal(s.id, "score", v)}
                    className="rounded border border-slate-300 px-2 py-1 text-xs hover:bg-slate-100"
                  >
                    {v}
                  </button>
                ))}
              </div>
              <input
                type="range"
                min={0}
                max={s.max_score}
                step={0.25}
                value={local.score}
                onChange={(e) => updateLocal(s.id, "score", parseFloat(e.target.value))}
                className="flex-1 accent-emerald-500"
              />
              <input
                type="number"
                step={0.25}
                min={0}
                max={s.max_score}
                value={local.score}
                onChange={(e) => updateLocal(s.id, "score", parseFloat(e.target.value) || 0)}
                className="w-20 px-3 py-2 bg-slate-50 dark:bg-slate-800/50 border border-slate-200 dark:border-slate-700 rounded-lg text-center font-bold text-lg focus:outline-none focus:ring-2 focus:ring-emerald-500/20 focus:border-emerald-500"
              />
            </div>

            <textarea
              value={local.comment}
              onChange={(e) => updateLocal(s.id, "comment", e.target.value)}
              placeholder="Comentário do professor (opcional)..."
              className="w-full bg-slate-50 dark:bg-slate-800/50 border border-slate-200 dark:border-slate-700 rounded-lg p-3 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/20 focus:border-emerald-500 resize-none h-16"
            />
          </div>
        );
      })}

      {autoScores.length > 0 && (
        <details className="rounded-xl border border-slate-200 dark:border-slate-700 bg-white/40 dark:bg-slate-900/40 p-4">
          <summary className="cursor-pointer text-sm font-medium text-slate-600 dark:text-slate-300">
            Questões já autoaprovadas ({autoScores.length}) — contexto apenas
          </summary>
          <ul className="mt-3 space-y-2 text-sm text-slate-600 dark:text-slate-400">
            {autoScores.map((s) => (
              <li key={s.id}>
                Q{s.question_number}: <span className="font-semibold text-emerald-600">{(s.final_score ?? s.ai_score).toFixed(2)}</span>
                {" / "}{s.max_score.toFixed(2)}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
