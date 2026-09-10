"use client";

import { FormEvent, useState } from "react";
import axios from "axios";
import { useRouter } from "next/navigation";
import { AlertTriangle, CheckCircle2, Download, FileSearch, Loader2, Save } from "lucide-react";
import { uploadApi } from "@/lib/api";
import DiscursiveLayoutEditor, {
  type DiscursiveLayoutPage,
  type DiscursiveLayoutQuestion,
} from "@/components/DiscursiveLayoutEditor";

type DetectResponse = {
  ok: true;
  source_format: "pdf" | "docx";
  suggested_title: string;
  pages: DiscursiveLayoutPage[];
  questions: DiscursiveLayoutQuestion[];
  warnings: string[];
  requires_confirmation: boolean;
  canonical_pdf_data_url?: string;
};

type ConfirmResponse = {
  ok: true;
  exam_id: string;
  title: string;
  questions_created: number;
  template_page_count: number;
  warnings: string[];
  next_step: string;
};

function apiMessage(error: unknown, fallback: string) {
  if (!axios.isAxiosError(error)) return fallback;
  const detail = error.response?.data?.detail;
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object") {
    if (Array.isArray(detail.errors) && detail.errors.length) return detail.errors.join(" ");
    return String(detail.message || detail.detail || fallback);
  }
  return fallback;
}

export default function DiscursiveImportPage() {
  const router = useRouter();
  const [file, setFile] = useState<File | null>(null);
  const [detecting, setDetecting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [detected, setDetected] = useState<DetectResponse | null>(null);
  const [title, setTitle] = useState("");
  const [questions, setQuestions] = useState<DiscursiveLayoutQuestion[]>([]);
  const [saved, setSaved] = useState<ConfirmResponse | null>(null);

  const detectLayout = async (event: FormEvent) => {
    event.preventDefault();
    if (!file) {
      setError("Selecione uma prova discursiva em PDF ou DOCX.");
      return;
    }

    setDetecting(true);
    setError("");
    setSaved(null);
    try {
      const body = new FormData();
      body.append("file", file);
      const { data } = await uploadApi.post<DetectResponse>("/discursive-import/detect", body);
      setDetected(data);
      setTitle(data.suggested_title || "Prova discursiva externa");
      setQuestions(data.questions || []);
    } catch (err) {
      setDetected(null);
      setQuestions([]);
      setError(apiMessage(err, "Não foi possível detectar o layout da prova."));
    } finally {
      setDetecting(false);
    }
  };

  const confirmLayout = async () => {
    if (!detected) return;
    if (!title.trim()) {
      setError("Informe um nome para a prova.");
      return;
    }
    if (!questions.length) {
      setError("Adicione ao menos uma questão e marque sua área de resposta.");
      return;
    }

    setSaving(true);
    setError("");
    try {
      const payload = {
        title: title.trim(),
        pages: detected.pages.map(({ page_index, width_pt, height_pt }) => ({
          page_index,
          width_pt,
          height_pt,
        })),
        questions: questions.map((question) => ({
          ...question,
          expected_answer: question.expected_answer || "",
          correction_criteria: question.correction_criteria || null,
          max_score: question.max_score || 1,
        })),
      };
      const { data } = await uploadApi.post<ConfirmResponse>("/discursive-import/confirm", payload);
      setSaved(data);
    } catch (err) {
      setError(apiMessage(err, "Não foi possível salvar o layout confirmado."));
    } finally {
      setSaving(false);
    }
  };

  const downloadCanonicalPdf = () => {
    if (!detected?.canonical_pdf_data_url) return;
    const link = document.createElement("a");
    link.href = detected.canonical_pdf_data_url;
    link.download = `${title.trim() || "prova_discursiva"}_para_impressao.pdf`;
    document.body.appendChild(link);
    link.click();
    link.remove();
  };

  return (
    <div className="space-y-6 animate-in fade-in slide-in-from-bottom-4 duration-500">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Importar prova discursiva</h1>
          <p className="mt-1 max-w-3xl text-slate-500">
            Use uma prova externa contendo somente questões discursivas. O sistema detecta as áreas de resposta e você confirma o mapa antes da correção manuscrita.
          </p>
        </div>
        <FileSearch className="h-9 w-9 text-emerald-600" />
      </header>

      <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
        <strong>Importante:</strong> este recurso é exclusivo para provas discursivas. O fluxo das provas práticas não é alterado.
      </div>

      <form onSubmit={detectLayout} className="glass-panel space-y-4 rounded-xl p-6">
        <label className="block space-y-2">
          <span className="text-sm font-medium text-slate-700">Prova original sem respostas</span>
          <input
            type="file"
            accept="application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,.pdf,.docx"
            onChange={(event) => {
              setFile(event.target.files?.[0] || null);
              setDetected(null);
              setSaved(null);
              setError("");
            }}
            className="block w-full text-sm file:mr-4 file:rounded-lg file:border-0 file:bg-emerald-50 file:px-4 file:py-2 file:font-medium file:text-emerald-700 hover:file:bg-emerald-100"
          />
        </label>
        <button
          type="submit"
          disabled={!file || detecting}
          className="inline-flex items-center gap-2 rounded-lg bg-emerald-600 px-4 py-2 font-medium text-white hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {detecting ? <Loader2 className="h-4 w-4 animate-spin" /> : <FileSearch className="h-4 w-4" />}
          {detecting ? "Detectando layout..." : "Detectar questões e áreas"}
        </button>
      </form>

      {error ? (
        <div className="flex items-start gap-3 rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-800">
          <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0" />
          <span>{error}</span>
        </div>
      ) : null}

      {detected ? (
        <div className="space-y-6">
          <section className="glass-panel space-y-4 rounded-xl p-6">
            <div className="grid gap-4 md:grid-cols-[1fr_auto] md:items-end">
              <label className="block space-y-2">
                <span className="text-sm font-medium text-slate-700">Nome da prova</span>
                <input
                  value={title}
                  onChange={(event) => setTitle(event.target.value)}
                  className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2"
                  maxLength={200}
                />
              </label>
              <div className="text-sm text-slate-600">
                <strong>{questions.length}</strong> questão(ões) em <strong>{detected.pages.length}</strong> página(s)
              </div>
            </div>

            {detected.warnings.length ? (
              <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
                <p className="font-medium">Confira estes pontos:</p>
                <ul className="mt-1 list-disc space-y-1 pl-5">
                  {detected.warnings.map((warning, index) => (
                    <li key={`${warning}-${index}`}>{warning}</li>
                  ))}
                </ul>
              </div>
            ) : null}

            {detected.source_format === "docx" && detected.canonical_pdf_data_url ? (
              <div className="rounded-lg border border-sky-200 bg-sky-50 p-4 text-sm text-sky-900">
                <p className="font-medium">DOCX convertido para uma versão canônica de impressão</p>
                <p className="mt-1">
                  Para que as coordenadas coincidam com os scans dos alunos, imprima e aplique esta versão em PDF. Não use uma impressão diferente do DOCX original depois de confirmar o mapa.
                </p>
                <button
                  type="button"
                  onClick={downloadCanonicalPdf}
                  className="mt-3 inline-flex items-center gap-2 rounded-lg border border-sky-300 bg-white px-3 py-2 font-medium text-sky-800 hover:bg-sky-100"
                >
                  <Download className="h-4 w-4" />
                  Baixar PDF para impressão
                </button>
              </div>
            ) : null}
          </section>

          <DiscursiveLayoutEditor pages={detected.pages} questions={questions} onChange={setQuestions} />

          <section className="glass-panel sticky bottom-4 z-20 flex flex-col gap-3 rounded-xl p-4 shadow-lg md:flex-row md:items-center md:justify-between">
            <div className="text-sm text-slate-600">
              Confirme que cada caixa verde/âmbar cobre apenas o espaço em que o aluno escreverá.
            </div>
            <button
              type="button"
              onClick={confirmLayout}
              disabled={saving || !questions.length}
              className="inline-flex items-center justify-center gap-2 rounded-lg bg-emerald-600 px-5 py-2.5 font-medium text-white hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
              {saving ? "Salvando..." : "Confirmar e salvar prova"}
            </button>
          </section>
        </div>
      ) : null}

      {saved ? (
        <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-5 text-emerald-900">
          <div className="flex items-start gap-3">
            <CheckCircle2 className="mt-0.5 h-6 w-6 shrink-0" />
            <div className="flex-1">
              <p className="font-semibold">Layout salvo com sucesso</p>
              <p className="mt-1 text-sm">{saved.next_step}</p>
              <button
                type="button"
                onClick={() => router.push(`/exams/${saved.exam_id}/edit`)}
                className="mt-3 rounded-lg bg-emerald-700 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-800"
              >
                Cadastrar resposta esperada e critérios
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
