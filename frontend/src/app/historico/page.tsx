"use client";

import { useEffect, useMemo, useState } from "react";
import { Download, History, Loader2, RefreshCcw } from "lucide-react";
import { api, visualExamAnalysisApi } from "@/lib/api";

type HistoryItem = {
  id: string;
  kind: "batch" | "visual";
  exam_name: string;
  is_practical: boolean;
  status: string;
  created_at: string | null;
  filename: string;
  students_count: number;
  questions_count: number;
  pending_review_count: number;
  export_path: string;
};

type HistoryResponse = {
  items: HistoryItem[];
};

function formatDate(value: string | null) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "-";
  return new Intl.DateTimeFormat("pt-BR", {
    dateStyle: "short",
    timeStyle: "short",
  }).format(date);
}

function statusLabel(value: string) {
  const labels: Record<string, string> = {
    PENDING: "Pendente",
    PROCESSING: "Processando",
    REVIEW_PENDING: "Revisão pendente",
    DONE: "Concluída",
    FAILED: "Falhou",
    SUCCESS: "Concluída",
  };
  return labels[value] || value;
}

export default function HistoryPage() {
  const [items, setItems] = useState<HistoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [exportingId, setExportingId] = useState("");

  const totals = useMemo(
    () => ({
      corrections: items.length,
      pending: items.filter((item) => item.pending_review_count > 0).length,
      students: items.reduce((sum, item) => sum + item.students_count, 0),
    }),
    [items],
  );

  const loadHistory = async () => {
    setLoading(true);
    setError("");
    try {
      const { data } = await api.get<HistoryResponse>("/history/corrections");
      setItems(data.items || []);
    } catch {
      setError("Não foi possível carregar o histórico de correções.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void loadHistory();
    }, 0);
    return () => window.clearTimeout(timer);
  }, []);

  const exportSpreadsheet = async (item: HistoryItem) => {
    setExportingId(item.id);
    setError("");
    try {
      const client = item.kind === "visual" ? visualExamAnalysisApi : api;
      const response = await client.get(item.export_path, {
        params: { include_details: true },
        responseType: "blob",
      });
      const blobUrl = window.URL.createObjectURL(new Blob([response.data]));
      const a = document.createElement("a");
      a.href = blobUrl;
      a.setAttribute("download", `historico_${item.kind}_${item.id}.xlsx`);
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(blobUrl);
    } catch {
      setError("Não foi possível exportar a planilha desta correção.");
    } finally {
      setExportingId("");
    }
  };

  return (
    <div className="space-y-6 animate-in fade-in slide-in-from-bottom-4 duration-500">
      <div className="flex items-center justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Histórico de Correções</h1>
          <p className="text-slate-500 mt-1">Correções realizadas pelo professor logado.</p>
        </div>
        <History className="w-9 h-9 text-emerald-600" />
      </div>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
        <div className="rounded-xl border border-surface-border bg-white/70 p-4">
          <div className="text-xs uppercase text-slate-500">Correções</div>
          <div className="mt-1 text-2xl font-bold">{totals.corrections}</div>
        </div>
        <div className="rounded-xl border border-surface-border bg-white/70 p-4">
          <div className="text-xs uppercase text-slate-500">Com revisão pendente</div>
          <div className="mt-1 text-2xl font-bold text-amber-700">{totals.pending}</div>
        </div>
        <div className="rounded-xl border border-surface-border bg-white/70 p-4">
          <div className="text-xs uppercase text-slate-500">Alunos processados</div>
          <div className="mt-1 text-2xl font-bold">{totals.students}</div>
        </div>
      </div>

      <div className="glass-panel rounded-xl overflow-hidden">
        <div className="flex items-center justify-between gap-3 border-b border-surface-border p-5">
          <div>
            <h2 className="text-lg font-bold">Correções salvas</h2>
            <p className="text-sm text-slate-500">Use o XLSX para revisar ou arquivar a correção final.</p>
          </div>
          <button
            type="button"
            onClick={() => void loadHistory()}
            disabled={loading}
            className="btn-secondary inline-flex items-center gap-2 disabled:opacity-60"
          >
            {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCcw className="h-4 w-4" />}
            <span>Atualizar</span>
          </button>
        </div>

        {error ? (
          <div className="mx-5 mt-4 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
            {error}
          </div>
        ) : null}

        {loading ? (
          <div className="flex h-48 items-center justify-center">
            <Loader2 className="h-8 w-8 animate-spin text-emerald-600" />
          </div>
        ) : items.length === 0 ? (
          <div className="p-8 text-center text-sm text-slate-500">Nenhuma correção encontrada para este professor.</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-left text-sm">
              <thead>
                <tr className="bg-slate-50/70 text-xs uppercase text-slate-500 dark:bg-slate-800/40">
                  <th className="px-4 py-3">Data</th>
                  <th className="px-4 py-3">Origem</th>
                  <th className="px-4 py-3">Prova/arquivo</th>
                  <th className="px-4 py-3">Status</th>
                  <th className="px-4 py-3">Alunos</th>
                  <th className="px-4 py-3">Questões</th>
                  <th className="px-4 py-3">Pendências</th>
                  <th className="px-4 py-3 text-right">Ações</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-surface-border">
                {items.map((item) => (
                  <tr key={`${item.kind}-${item.id}`}>
                    <td className="px-4 py-3 whitespace-nowrap">{formatDate(item.created_at)}</td>
                    <td className="px-4 py-3">
                      {item.kind === "visual" ? "Manuscrita" : item.is_practical ? "Prática" : "Discursiva"}
                    </td>
                    <td className="px-4 py-3 font-medium">{item.exam_name || item.filename}</td>
                    <td className="px-4 py-3">{statusLabel(item.status)}</td>
                    <td className="px-4 py-3">{item.students_count}</td>
                    <td className="px-4 py-3">{item.questions_count}</td>
                    <td className="px-4 py-3">
                      <span className={item.pending_review_count > 0 ? "font-semibold text-amber-700" : "text-slate-500"}>
                        {item.pending_review_count}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-right">
                      <button
                        type="button"
                        onClick={() => void exportSpreadsheet(item)}
                        disabled={exportingId === item.id || item.questions_count === 0}
                        className="btn-secondary inline-flex items-center gap-2 disabled:opacity-60"
                      >
                        {exportingId === item.id ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
                        <span>XLSX</span>
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
