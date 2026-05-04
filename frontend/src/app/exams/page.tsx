"use client";

import { type ChangeEvent, useCallback, useEffect, useRef, useState } from 'react';
import {
  Plus,
  BookOpen,
  FileDown,
  FileUp,
  Loader2,
  Pencil,
  Trash2,
} from 'lucide-react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { api, uploadApi } from '@/lib/api';

type ExamSummary = {
  id: string;
  name: string;
  class_id: string | null;
  question_count: number;
  is_practical: boolean;
};

type ExamsPageMode = 'default' | 'practical';

type ExamsPageProps = {
  mode?: ExamsPageMode;
};

export function ExamsPageContent({ mode = 'default' }: ExamsPageProps) {
  const router = useRouter();
  const [exams, setExams] = useState<ExamSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [downloadingId, setDownloadingId] = useState<string | null>(null);
  const [pendingLogoExam, setPendingLogoExam] = useState<{ id: string; name: string } | null>(null);
  const logoInputRef = useRef<HTMLInputElement | null>(null);
  const docxInputRef = useRef<HTMLInputElement | null>(null);
  const [downloadingTemplate, setDownloadingTemplate] = useState(false);
  const [importingDocx, setImportingDocx] = useState(false);
  const [importMessage, setImportMessage] = useState<string | null>(null);
  const [importWarnings, setImportWarnings] = useState<string[]>([]);

  const isPracticalMode = mode === 'practical';

  const loadExams = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const { data } = await api.get<ExamSummary[]>('/exams', {
        params: { practical: isPracticalMode },
      });
      setExams(data);
    } catch {
      setError('Não foi possível carregar as provas.');
    } finally {
      setLoading(false);
    }
  }, [isPracticalMode]);

  useEffect(() => {
    void loadExams();
  }, [loadExams]);

  const pageTitle = isPracticalMode ? 'Provas Práticas' : 'Provas';
  const pageSubtitle = isPracticalMode
    ? 'Mesma configuração de provas, com folhas práticas compactas (1 linha de resposta por questão).'
    : 'Gabaritos e questões para correção assistida.';

  const handleDownloadSheets = async (examId: string, examName: string, logoFile: File) => {
    setDownloadingId(examId);
    setError(null);
    try {
      const formData = new FormData();
      formData.append('logo', logoFile);
      const response = await uploadApi.post(
        isPracticalMode
          ? `/exams/${examId}/answer-sheets/practical`
          : `/exams/${examId}/answer-sheets`,
        formData,
        { responseType: 'blob' },
      );
      const url = window.URL.createObjectURL(new Blob([response.data]));
      const link = document.createElement('a');
      link.href = url;
      link.setAttribute('download', `folhas_${examName}.pdf`);
      document.body.appendChild(link);
      link.click();
      link.parentNode?.removeChild(link);
      window.URL.revokeObjectURL(url);
    } catch (err: unknown) {
      let msg = 'Erro ao gerar folhas-resposta.';
      if (err && typeof err === 'object' && 'response' in err) {
        const resp = (err as { response?: { data?: Blob } }).response;
        if (resp?.data instanceof Blob) {
          try {
            const text = await resp.data.text();
            const json = JSON.parse(text);
            if (json.detail) msg = json.detail;
          } catch { /* keep default msg */ }
        }
      }
      setError(msg);
    } finally {
      setDownloadingId(null);
    }
  };

  const handleRequestLogoAndDownload = (examId: string, examName: string) => {
    setPendingLogoExam({ id: examId, name: examName });
    logoInputRef.current?.click();
  };

  const handleLogoSelected = async (event: ChangeEvent<HTMLInputElement>) => {
    const selectedLogo = event.target.files?.[0];
    const exam = pendingLogoExam;
    event.target.value = '';
    setPendingLogoExam(null);

    if (!exam || !selectedLogo) return;
    await handleDownloadSheets(exam.id, exam.name, selectedLogo);
  };

  const handleDelete = async (examId: string, examName: string) => {
    if (!confirm(`Excluir a prova "${examName}" e todas as suas questões?`)) return;
    setDeletingId(examId);
    setError(null);
    try {
      await api.delete(`/exams/${examId}`);
      setExams((prev) => prev.filter((e) => e.id !== examId));
    } catch {
      setError('Erro ao excluir a prova.');
    } finally {
      setDeletingId(null);
    }
  };

  const handleDownloadDocxTemplate = async () => {
    setDownloadingTemplate(true);
    setError(null);
    try {
      const response = await api.get('/exams/templates/discursive-docx', { responseType: 'blob' });
      const url = window.URL.createObjectURL(new Blob([response.data]));
      const link = document.createElement('a');
      link.href = url;
      link.setAttribute('download', 'template_prova_discursiva.docx');
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);
    } catch {
      setError('Erro ao baixar template DOCX.');
    } finally {
      setDownloadingTemplate(false);
    }
  };

  const handleDocxSelected = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;
    setImportingDocx(true);
    setError(null);
    setImportMessage(null);
    setImportWarnings([]);
    try {
      const formData = new FormData();
      formData.append('file', file);
      const q = isPracticalMode ? '?practical=true' : '?practical=false';
      const { data } = await uploadApi.post<{
        ok: boolean;
        exam_id: string;
        title: string;
        questions_created: number;
        warnings: string[];
      }>(`/exams/import-discursive-docx${q}`, formData);
      setImportMessage(`Prova criada com sucesso. ${data.questions_created} questões importadas.`);
      setImportWarnings(data.warnings || []);
      await loadExams();
      router.push(`/exams/${data.exam_id}/edit`);
    } catch (err: unknown) {
      const detail = (
        err &&
        typeof err === 'object' &&
        'response' in err &&
        (err as { response?: { data?: { detail?: { message?: string; detail?: string } } } }).response?.data?.detail
      ) as { message?: string; detail?: string } | undefined;
      setError(detail?.message || detail?.detail || 'Erro ao importar DOCX.');
    } finally {
      setImportingDocx(false);
    }
  };

  return (
    <div className="space-y-8 animate-in fade-in duration-500">
      <input
        ref={logoInputRef}
        type="file"
        accept="image/png,image/jpeg,image/jpg,image/webp"
        className="hidden"
        onChange={handleLogoSelected}
      />
      <input
        ref={docxInputRef}
        type="file"
        accept=".docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        className="hidden"
        onChange={handleDocxSelected}
      />

      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">{pageTitle}</h1>
          <p className="text-slate-500 mt-1 text-sm">{pageSubtitle}</p>
        </div>
      </div>

      {loading && (
        <div className="flex items-center gap-2 text-slate-500">
          <Loader2 className="w-5 h-5 animate-spin" />
          <span>Carregando...</span>
        </div>
      )}

      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 dark:bg-red-900/20 px-4 py-3 text-sm text-red-700 dark:text-red-300">
          {error}
        </div>
      )}

      <div className="glass-panel rounded-xl p-5 border border-surface-border">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div className="max-w-3xl">
            <h2 className="text-lg font-bold text-slate-800 dark:text-slate-100">
              Importar prova discursiva por DOCX
            </h2>
            <p className="mt-1 text-sm text-slate-500">
              Baixe o modelo, preencha os dados gerais e as questões (enunciado e resposta esperada), depois envie o arquivo para criar a prova automaticamente.
            </p>
            {importMessage ? (
              <p className="mt-2 text-sm font-medium text-emerald-700">{importMessage}</p>
            ) : null}
            {importWarnings.length > 0 ? (
              <div className="mt-2 rounded border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
                Prova importada com avisos: {importWarnings.slice(0, 2).join(' | ')}
              </div>
            ) : null}
          </div>
          <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap lg:flex-nowrap lg:justify-end">
            <Link href={isPracticalMode ? '/provas-praticas/new' : '/exams/new'}>
              <button
                type="button"
                className="btn-primary inline-flex h-10 w-full items-center justify-center gap-2 whitespace-nowrap px-3 text-sm font-medium shadow-emerald-500/20 shadow-lg sm:w-auto"
              >
                <Plus className="h-4 w-4" />
                Nova prova
              </button>
            </Link>
            <button
              type="button"
              onClick={handleDownloadDocxTemplate}
              disabled={downloadingTemplate}
              className="btn-primary inline-flex h-10 w-full items-center justify-center gap-2 whitespace-nowrap px-3 text-sm font-medium sm:w-auto"
            >
              {downloadingTemplate ? <Loader2 className="h-4 w-4 animate-spin" /> : <FileDown className="h-4 w-4" />}
              Template DOCX
            </button>
            <button
              type="button"
              onClick={() => docxInputRef.current?.click()}
              disabled={importingDocx}
              className="btn-primary inline-flex h-10 w-full items-center justify-center gap-2 whitespace-nowrap px-3 text-sm font-medium sm:w-auto"
            >
              {importingDocx ? <Loader2 className="h-4 w-4 animate-spin" /> : <FileUp className="h-4 w-4" />}
              Upload template
            </button>
          </div>
        </div>
      </div>

      <div className="glass-panel rounded-xl overflow-hidden shadow-sm">
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="bg-slate-50/50 dark:bg-slate-800/30 text-slate-500 text-xs uppercase tracking-wider font-semibold">
                <th className="px-6 py-4">Prova</th>
                <th className="px-6 py-4">Questões</th>
                <th className="px-6 py-4">Turma</th>
                <th className="px-6 py-4 text-right">Ações</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-surface-border">
              {exams.length === 0 && !loading ? (
                <tr>
                  <td colSpan={4} className="px-6 py-8 text-center text-slate-500 text-sm">
                    Nenhuma prova. Clique em &quot;Criar Nova Prova&quot;.
                  </td>
                </tr>
              ) : (
                exams.map((ex) => (
                  <tr key={ex.id} className="hover:bg-slate-50/50 dark:hover:bg-slate-800/20 transition-colors">
                    <td className="px-6 py-4">
                      <div className="flex items-center space-x-3">
                        <div className="p-2 bg-emerald-50 dark:bg-emerald-900/20 rounded-lg">
                          <BookOpen className="w-5 h-5 text-emerald-600" />
                        </div>
                        <div className="font-medium text-slate-800 dark:text-slate-200">{ex.name}</div>
                      </div>
                    </td>
                    <td className="px-6 py-4 text-sm text-slate-600">{ex.question_count}</td>
                    <td className="px-6 py-4 text-sm text-slate-600">
                      {ex.class_id ? (
                        <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-blue-50 text-blue-700 dark:bg-blue-900/20 dark:text-blue-400">
                          Vinculada
                        </span>
                      ) : (
                        <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-amber-50 text-amber-700 dark:bg-amber-900/20 dark:text-amber-400">
                          Sem turma
                        </span>
                      )}
                    </td>
                    <td className="px-6 py-4 text-right">
                      <div className="flex items-center justify-end gap-2">
                        <button
                          type="button"
                          onClick={() => handleRequestLogoAndDownload(ex.id, ex.name)}
                          disabled={!ex.class_id || ex.question_count === 0 || downloadingId === ex.id}
                          className="p-2 bg-emerald-50 text-emerald-700 hover:bg-emerald-100 dark:bg-emerald-900/20 dark:text-emerald-400 transition-colors rounded-lg inline-flex items-center space-x-1.5 text-xs font-medium border border-emerald-200 dark:border-emerald-800 disabled:opacity-40 disabled:cursor-not-allowed"
                          title={!ex.class_id ? "Vincule a prova a uma turma primeiro" : ex.question_count === 0 ? "Adicione questões primeiro" : "Selecionar logo da IESE e baixar folhas-resposta"}
                        >
                          {downloadingId === ex.id ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <FileDown className="w-3.5 h-3.5" />}
                          <span>Folhas</span>
                        </button>

                        <Link href={`/exams/${ex.id}/edit`}>
                          <button
                            type="button"
                            className="p-2 bg-slate-100 text-slate-600 hover:text-blue-600 hover:bg-blue-50 dark:bg-slate-800 dark:text-slate-400 dark:hover:bg-blue-900/30 transition-colors rounded-lg inline-flex items-center space-x-1.5 text-xs font-medium border border-slate-200 dark:border-slate-700"
                            title="Editar prova"
                          >
                            <Pencil className="w-3.5 h-3.5" />
                            <span>Editar</span>
                          </button>
                        </Link>

                        <button
                          type="button"
                          onClick={() => handleDelete(ex.id, ex.name)}
                          disabled={deletingId === ex.id}
                          className="p-2 bg-red-50 text-red-600 hover:bg-red-100 dark:bg-red-900/20 dark:text-red-400 transition-colors rounded-lg inline-flex items-center space-x-1.5 text-xs font-medium border border-red-200 dark:border-red-800 disabled:opacity-40"
                          title="Excluir prova"
                        >
                          {deletingId === ex.id ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Trash2 className="w-3.5 h-3.5" />}
                          <span>Excluir</span>
                        </button>
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

    </div>
  );
}

export default function ExamsPage() {
  return <ExamsPageContent mode="default" />;
}
