"use client";

import { useEffect, useState } from 'react';
import { UploadCloud, CheckCircle, Clock, FileText, Loader2, Download } from 'lucide-react';
import Link from 'next/link';
import UploadModal from '@/components/UploadModal';
import { api } from '@/lib/api';

type ExamSummary = { id: string; name: string; question_count: number };

export default function Dashboard() {
  const [isUploadModalOpen, setIsUploadModalOpen] = useState(false);
  const [exams, setExams] = useState<ExamSummary[]>([]);
  const [loading, setLoading] = useState(true);

  const load = async () => {
    setLoading(true);
    try {
      const { data } = await api.get<ExamSummary[]>('/exams', { params: { practical: false } });
      setExams(data);
    } catch { /* silent */ }
    setLoading(false);
  };

  useEffect(() => { load(); }, []);

  const handleUploadSuccess = () => {
    setIsUploadModalOpen(false);
    load();
  };

  return (
    <div className="space-y-8 animate-in fade-in slide-in-from-bottom-4 duration-500">
      <UploadModal
        isOpen={isUploadModalOpen}
        onClose={() => setIsUploadModalOpen(false)}
        onUploadSuccess={handleUploadSuccess}
      />

      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Visão Geral</h1>
          <p className="text-slate-500 mt-1">Gerencie provas, envie PDFs escaneados e revise correções.</p>
        </div>
        <button
          onClick={() => setIsUploadModalOpen(true)}
          className="btn-primary flex items-center space-x-2 shadow-emerald-500/20 shadow-lg"
        >
          <UploadCloud className="w-5 h-5" />
          <span>Enviar PDF Escaneado</span>
        </button>
      </div>

      {/* Fluxo resumido */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <Link href="/classes" className="glass-panel p-5 rounded-xl border border-surface-border hover:border-emerald-300 transition-colors group">
          <div className="text-emerald-600 font-bold text-lg group-hover:text-emerald-700">1.</div>
          <div className="font-semibold mt-1">Turmas & CSV</div>
          <p className="text-xs text-slate-500 mt-1">Crie turma e importe alunos</p>
        </Link>
        <Link href="/exams/new" className="glass-panel p-5 rounded-xl border border-surface-border hover:border-emerald-300 transition-colors group">
          <div className="text-emerald-600 font-bold text-lg group-hover:text-emerald-700">2.</div>
          <div className="font-semibold mt-1">Criar Prova</div>
          <p className="text-xs text-slate-500 mt-1">Questões + gabarito</p>
        </Link>
        <Link href="/exams" className="glass-panel p-5 rounded-xl border border-surface-border hover:border-emerald-300 transition-colors group">
          <div className="text-emerald-600 font-bold text-lg group-hover:text-emerald-700">3.</div>
          <div className="font-semibold mt-1">Folhas-Resposta</div>
          <p className="text-xs text-slate-500 mt-1">Baixe PDF para imprimir</p>
        </Link>
        <Link href="/review" className="glass-panel p-5 rounded-xl border border-surface-border hover:border-emerald-300 transition-colors group">
          <div className="text-emerald-600 font-bold text-lg group-hover:text-emerald-700">4.</div>
          <div className="font-semibold mt-1">Revisar Notas</div>
          <p className="text-xs text-slate-500 mt-1">IA corrige, você confirma</p>
        </Link>
      </div>

      {/* Provas */}
      <div className="glass-panel rounded-xl overflow-hidden shadow-sm">
        <div className="p-6 border-b border-surface-border flex justify-between items-center">
          <h3 className="text-lg font-bold">Provas Cadastradas</h3>
          <Link href="/exams/new" className="text-sm text-emerald-600 font-medium hover:underline">+ Nova</Link>
        </div>
        {loading ? (
          <div className="p-12 flex justify-center">
            <Loader2 className="w-6 h-6 animate-spin text-emerald-600" />
          </div>
        ) : exams.length === 0 ? (
          <div className="p-12 text-center text-slate-500">
            Nenhuma prova. <Link href="/exams/new" className="text-emerald-600 font-medium hover:underline">Criar primeira prova</Link>.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse">
              <thead>
                <tr className="bg-slate-50/50 dark:bg-slate-800/30 text-slate-500 text-xs uppercase tracking-wider font-semibold">
                  <th className="px-6 py-4">Prova</th>
                  <th className="px-6 py-4">Questões</th>
                  <th className="px-6 py-4 text-right">Ação</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-surface-border">
                {exams.map((e) => (
                  <tr key={e.id} className="hover:bg-slate-50/50 dark:hover:bg-slate-800/20 transition-colors">
                    <td className="px-6 py-4">
                      <div className="flex items-center space-x-3">
                        <FileText className="w-5 h-5 text-emerald-600" />
                        <span className="font-medium text-slate-800 dark:text-slate-200">{e.name}</span>
                      </div>
                    </td>
                    <td className="px-6 py-4 text-sm text-slate-600">{e.question_count}</td>
                    <td className="px-6 py-4 text-right">
                      <Link href="/review">
                        <button className="text-sm text-emerald-700 dark:text-emerald-400 font-medium bg-emerald-50 dark:bg-emerald-500/10 px-3 py-1.5 rounded-lg hover:bg-emerald-100 transition-colors">
                          Revisar
                        </button>
                      </Link>
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
