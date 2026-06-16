"use client";

import { useEffect, useState } from 'react';
import { Plus, Trash2, Save, Loader2, ArrowLeft } from 'lucide-react';
import { useRouter, useParams } from 'next/navigation';
import Link from 'next/link';
import { api } from '@/lib/api';
import { useExamNav } from '@/contexts/ExamNavContext';

type ClassOption = {
  id: string;
  name: string;
  student_count: number;
};

type ExamData = {
  id: string;
  name: string;
  class_id: string | null;
  is_practical?: boolean;
};

type QuestionData = {
  id?: string;
  question_number: number;
  question_text: string;
  expected_answer: string;
  max_score: number;
  _isNew?: boolean;
};

type EditExamPageMode = 'default' | 'practical';

type EditExamPageContentProps = {
  mode?: EditExamPageMode;
};

export function EditExamPageContent({ mode = 'default' }: EditExamPageContentProps) {
  const router = useRouter();
  const params = useParams();
  const examId = params.id as string;
  const isPracticalRoute = mode === 'practical';
  const listPath = isPracticalRoute ? '/provas-praticas' : '/exams';
  const { setSection } = useExamNav();

  useEffect(() => {
    if (isPracticalRoute) {
      setSection('practical');
    }
    return () => setSection(null);
  }, [isPracticalRoute, setSection]);

  const [examName, setExamName] = useState("");
  const [classId, setClassId] = useState<string>("");
  const [classes, setClasses] = useState<ClassOption[]>([]);
  const [questions, setQuestions] = useState<QuestionData[]>([]);
  const [deletedQuestionIds, setDeletedQuestionIds] = useState<string[]>([]);

  const [loadingExam, setLoadingExam] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isPractical, setIsPractical] = useState(false);

  useEffect(() => {
    const load = async () => {
      try {
        const [examRes, questionsRes, classesRes] = await Promise.all([
          api.get<ExamData>(`/exams/${examId}`),
          api.get<QuestionData[]>(`/exams/${examId}/questions`),
          api.get<ClassOption[]>('/classes'),
        ]);
        setExamName(examRes.data.name);
        setClassId(examRes.data.class_id || "");
        setIsPractical(isPracticalRoute || Boolean(examRes.data.is_practical));
        if (examRes.data.is_practical) {
          setSection('practical');
        } else {
          setSection('discursive');
        }
        if (!isPracticalRoute && examRes.data.is_practical) {
          router.replace(`/provas-praticas/${examId}/edit`);
          return;
        }
        setQuestions(questionsRes.data.map((q) => ({ ...q, _isNew: false })));
        setClasses(classesRes.data);
      } catch {
        setError("Não foi possível carregar a prova.");
      } finally {
        setLoadingExam(false);
      }
    };
    load();
  }, [examId, isPracticalRoute, router, setSection]);

  const addQuestion = () => {
    setQuestions([
      ...questions,
      {
        question_number: questions.length + 1,
        question_text: "",
        expected_answer: "",
        max_score: 1.0,
        _isNew: true,
      },
    ]);
  };

  const removeQuestion = (idx: number) => {
    const q = questions[idx];
    if (q.id && !q._isNew) {
      setDeletedQuestionIds((prev) => [...prev, q.id!]);
    }
    const updated = questions.filter((_, i) => i !== idx).map((q, i) => ({ ...q, question_number: i + 1 }));
    setQuestions(updated);
  };

  const updateQuestion = (idx: number, field: keyof QuestionData, value: string | number) => {
    const updated = [...questions];
    (updated[idx] as Record<string, unknown>)[field] = value;
    setQuestions(updated);
  };

  const handleSave = async () => {
    if (!examName.trim()) {
      setError("Digite o nome da prova.");
      return;
    }
    if (questions.some((q) => !q.question_text.trim() || !q.expected_answer.trim())) {
      setError("Preencha o enunciado e o gabarito de todas as questões.");
      return;
    }

    setSaving(true);
    setError(null);

    try {
      await api.put(`/exams/${examId}`, {
        name: examName.trim(),
        class_id: classId || null,
        is_practical: isPracticalRoute || isPractical,
      });

      for (const qId of deletedQuestionIds) {
        await api.delete(`/exams/${examId}/questions/${qId}`);
      }

      for (const q of questions) {
        if (q._isNew) {
          await api.post(`/exams/${examId}/questions`, {
            question_number: q.question_number,
            question_text: q.question_text,
            expected_answer: q.expected_answer,
            max_score: q.max_score,
          });
        } else if (q.id) {
          await api.put(`/exams/${examId}/questions/${q.id}`, {
            question_number: q.question_number,
            question_text: q.question_text,
            expected_answer: q.expected_answer,
            max_score: q.max_score,
          });
        }
      }

      setDeletedQuestionIds([]);
      router.push(listPath);
    } catch {
      setError("Erro ao salvar alterações.");
    } finally {
      setSaving(false);
    }
  };

  if (loadingExam) {
    return (
      <div className="flex items-center justify-center py-20 gap-2 text-slate-500">
        <Loader2 className="w-5 h-5 animate-spin" />
        <span>Carregando prova...</span>
      </div>
    );
  }

  return (
    <div className="max-w-4xl mx-auto space-y-6 animate-in fade-in duration-500">
      <div className="flex items-center gap-4">
        <Link href={listPath}>
          <button type="button" className="p-2 rounded-lg hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors">
            <ArrowLeft className="w-5 h-5 text-slate-500" />
          </button>
        </Link>
        <div>
          <h1 className="text-3xl font-bold tracking-tight">
            {isPracticalRoute || isPractical ? 'Editar Prova Prática' : 'Editar Prova'}
          </h1>
          <p className="text-slate-500 mt-1">Altere nome, turma ou questões da prova.</p>
        </div>
      </div>

      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 dark:bg-red-900/20 px-4 py-3 text-sm text-red-700 dark:text-red-300">
          {error}
        </div>
      )}

      <div className="glass-panel rounded-xl p-6 border border-surface-border space-y-4">
        <div>
          <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-2">
            Nome da Prova
          </label>
          <input
            type="text"
            value={examName}
            onChange={(e) => setExamName(e.target.value)}
            placeholder="Ex: Prova 1 — Anatomia Humana"
            className="w-full bg-slate-50 dark:bg-slate-800/50 border border-slate-200 dark:border-slate-700 rounded-lg px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/20 focus:border-emerald-500"
          />
        </div>
        <div>
          <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-2">
            Turma
          </label>
          <select
            value={classId}
            onChange={(e) => setClassId(e.target.value)}
            className="w-full bg-slate-50 dark:bg-slate-800/50 border border-slate-200 dark:border-slate-700 rounded-lg px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/20 focus:border-emerald-500"
          >
            <option value="">Sem turma vinculada</option>
            {classes.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name} ({c.student_count} aluno{c.student_count !== 1 ? "s" : ""})
              </option>
            ))}
          </select>
        </div>
      </div>

      {questions.map((q, idx) => (
        <div key={q.id || `new-${idx}`} className="glass-panel rounded-xl p-6 border border-surface-border space-y-4">
          <div className="flex justify-between items-center">
            <h3 className="font-bold text-lg text-slate-800 dark:text-slate-200">
              Questão {q.question_number}
              {!q._isNew && <span className="text-xs font-normal text-slate-400 ml-2">(salva)</span>}
              {q._isNew && <span className="text-xs font-normal text-amber-500 ml-2">(nova)</span>}
            </h3>
            <div className="flex items-center gap-3">
              <label className="text-sm text-slate-500">Peso:</label>
              <select
                value={q.max_score}
                onChange={(e) => updateQuestion(idx, "max_score", parseFloat(e.target.value))}
                className="bg-slate-50 dark:bg-slate-800/50 border border-slate-200 dark:border-slate-700 rounded-lg px-3 py-1.5 text-sm disabled:opacity-60"
              >
                <option value={0.25}>0,25</option>
                <option value={0.5}>0,50</option>
                <option value={0.75}>0,75</option>
                <option value={1.0}>1,00</option>
              </select>
              <button
                type="button"
                onClick={() => removeQuestion(idx)}
                className="p-1.5 text-red-400 hover:text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20 rounded-lg transition-colors"
                title="Remover questão"
              >
                <Trash2 className="w-4 h-4" />
              </button>
            </div>
          </div>

          <div>
            <label className="block text-xs font-medium text-slate-500 mb-1">Enunciado</label>
            <textarea
              value={q.question_text}
              onChange={(e) => updateQuestion(idx, "question_text", e.target.value)}
              placeholder="Digite o enunciado da questão..."
              rows={2}
              className="w-full bg-slate-50 dark:bg-slate-800/50 border border-slate-200 dark:border-slate-700 rounded-lg px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/20 focus:border-emerald-500 resize-none disabled:opacity-60"
            />
          </div>

          <div>
            <label className="block text-xs font-medium text-slate-500 mb-1">
              Padrão de Resposta (Gabarito)
            </label>
            <textarea
              value={q.expected_answer}
              onChange={(e) => updateQuestion(idx, "expected_answer", e.target.value)}
              placeholder="Digite aqui a resposta esperada..."
              rows={4}
              className="w-full bg-slate-50 dark:bg-slate-800/50 border border-slate-200 dark:border-slate-700 rounded-lg px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/20 focus:border-emerald-500 resize-none disabled:opacity-60"
            />
          </div>
        </div>
      ))}

      <div className="flex justify-between items-center">
        <button
          type="button"
          onClick={addQuestion}
          className="btn-secondary flex items-center space-x-2"
        >
          <Plus className="w-4 h-4" />
          <span>Adicionar Questão</span>
        </button>

        <button
          type="button"
          onClick={handleSave}
          disabled={saving}
          className="btn-primary flex items-center space-x-2 shadow-emerald-500/20 shadow-lg disabled:opacity-50"
        >
          {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
          <span>Salvar Alterações</span>
        </button>
      </div>
    </div>
  );
}

export default function EditExamPage() {
  return <EditExamPageContent mode="default" />;
}
