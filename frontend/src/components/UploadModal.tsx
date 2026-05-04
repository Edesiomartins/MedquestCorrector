"use client";

import { useEffect, useState } from 'react';
import { X, Upload, File as FileIcon, CheckCircle2, AlertCircle, Loader2 } from 'lucide-react';
import { uploadApi, api } from '@/lib/api';

interface ExamOption {
  id: string;
  name: string;
  question_count: number;
}

interface UploadModalProps {
  isOpen: boolean;
  onClose: () => void;
  onUploadSuccess: () => void;
}

export default function UploadModal({ isOpen, onClose, onUploadSuccess }: UploadModalProps) {
  const [file, setFile] = useState<File | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [exams, setExams] = useState<ExamOption[]>([]);
  const [examId, setExamId] = useState<string>("");
  const [loadingExams, setLoadingExams] = useState(false);

  useEffect(() => {
    if (!isOpen) return;
    let cancelled = false;
    (async () => {
      setLoadingExams(true);
      setError(null);
      try {
        const { data } = await api.get<ExamOption[]>('/exams', { params: { practical: false } });
        if (cancelled) return;
        setExams(data);
        if (data.length) {
          setExamId(data[0].id);
        }
      } catch {
        if (!cancelled) setError('Não foi possível carregar a lista de provas. Verifique se a API está no ar.');
      } finally {
        if (!cancelled) setLoadingExams(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [isOpen]);

  if (!isOpen) return null;

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files[0]) {
      setFile(e.target.files[0]);
      setError(null);
    }
  };

  const handleUpload = async () => {
    if (!file) {
      setError('Por favor, selecione um arquivo PDF.');
      return;
    }
    if (!examId) {
      setError('Cadastre uma prova em Provas → Criar nova antes de enviar o lote.');
      return;
    }

    setIsUploading(true);
    setError(null);

    try {
      const formData = new FormData();
      formData.append('file', file);
      formData.append('exam_id', examId);

      await uploadApi.post('/batches/upload', formData);
      onUploadSuccess();
    } catch (err: unknown) {
      const ax = err as { response?: { data?: { detail?: string } } };
      const detail = ax.response?.data?.detail;
      if (typeof detail === 'string') setError(detail);
      else setError('Falha no envio. Confira se o Celery está rodando e o PDF é válido.');
    } finally {
      setIsUploading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 backdrop-blur-sm animate-in fade-in duration-200">
      <div className="bg-white dark:bg-slate-900 rounded-xl shadow-2xl w-full max-w-lg overflow-hidden border border-slate-200 dark:border-slate-800">

        <div className="flex justify-between items-center p-6 border-b border-slate-200 dark:border-slate-800">
          <h2 className="text-xl font-bold text-slate-800 dark:text-slate-100">Novo Lote de Provas</h2>
          <button type="button" onClick={onClose} className="text-slate-400 hover:text-slate-600 dark:hover:text-slate-300">
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="p-6 space-y-6">

          <div>
            <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1">Prova (gabarito cadastrado)</label>
            <select
              className="w-full border border-slate-300 dark:border-slate-700 rounded-lg p-2.5 bg-slate-50 dark:bg-slate-800 text-slate-900 dark:text-slate-100 outline-none focus:ring-2 focus:ring-emerald-500/50"
              value={examId}
              onChange={(e) => setExamId(e.target.value)}
              disabled={loadingExams || exams.length === 0}
            >
              {exams.length === 0 ? (
                <option value="">Nenhuma prova — crie uma em Provas</option>
              ) : (
                exams.map((ex) => (
                  <option key={ex.id} value={ex.id}>
                    {ex.name} ({ex.question_count} questões)
                  </option>
                ))
              )}
            </select>
            <p className="text-xs text-slate-500 mt-2">
              O PDF será associado a esta prova para processamento no worker (contagem de páginas e fila).
            </p>
          </div>

          <div className="border-2 border-dashed border-emerald-500/30 rounded-xl bg-emerald-50/50 dark:bg-emerald-900/10 p-8 flex flex-col items-center justify-center text-center transition-colors hover:bg-emerald-50 dark:hover:bg-emerald-900/20">
            {!file ? (
              <>
                <div className="w-12 h-12 bg-white dark:bg-slate-800 rounded-full flex items-center justify-center shadow-sm text-emerald-500 mb-4">
                  <Upload className="w-6 h-6" />
                </div>
                <p className="text-sm font-medium text-slate-700 dark:text-slate-300 mb-1">
                  Arraste o PDF escaneado aqui
                </p>
                <p className="text-xs text-slate-500 mb-4">Ou clique para buscar no computador</p>

                <label className="btn-secondary text-sm cursor-pointer border-emerald-200 dark:border-emerald-800 hover:border-emerald-300">
                  <span>Selecionar Arquivo</span>
                  <input type="file" accept="application/pdf" className="hidden" onChange={handleFileChange} />
                </label>
              </>
            ) : (
              <div className="flex items-center space-x-3 bg-white dark:bg-slate-800 p-4 rounded-lg shadow-sm border border-slate-200 dark:border-slate-700 w-full">
                <FileIcon className="w-8 h-8 text-emerald-500" />
                <div className="flex-1 text-left overflow-hidden">
                  <p className="text-sm font-medium text-slate-700 dark:text-slate-200 truncate">{file.name}</p>
                  <p className="text-xs text-slate-500">{(file.size / 1024 / 1024).toFixed(2)} MB</p>
                </div>
                <button type="button" onClick={() => setFile(null)} className="text-slate-400 hover:text-red-500 p-1">
                  <X className="w-4 h-4" />
                </button>
              </div>
            )}
          </div>

          {error && (
            <div className="flex items-center space-x-2 text-sm text-red-600 bg-red-50 dark:bg-red-900/20 p-3 rounded-lg border border-red-100 dark:border-red-800/30">
              <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
              <span>{error}</span>
            </div>
          )}

        </div>

        <div className="flex justify-end space-x-3 p-6 border-t border-slate-200 dark:border-slate-800 bg-slate-50 dark:bg-slate-800/30">
          <button type="button" onClick={onClose} disabled={isUploading} className="btn-secondary px-6">
            Cancelar
          </button>
          <button
            type="button"
            onClick={handleUpload}
            disabled={!file || isUploading || !examId}
            className={`btn-primary px-6 flex items-center space-x-2 ${(!file || isUploading || !examId) ? 'opacity-50 cursor-not-allowed' : ''}`}
          >
            {isUploading ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" />
                <span>Enviando...</span>
              </>
            ) : (
              <>
                <CheckCircle2 className="w-4 h-4" />
                <span>Iniciar processamento</span>
              </>
            )}
          </button>
        </div>

      </div>
    </div>
  );
}
