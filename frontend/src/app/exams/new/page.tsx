"use client";

import { useEffect, useState } from 'react';
import { Plus, Trash2, Save, Loader2 } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { api } from '@/lib/api';

type Question = {
  question_number: number;
  question_text: string;
  expected_answer: string;
  max_score: number;
};

type ClassOption = {
  id: string;
  name: string;
  student_count: number;
};

type NewExamPageContentProps = {
  isPractical?: boolean;
};

export function NewExamPageContent({ isPractical = false }: NewExamPageContentProps) {
  const router = useRouter();
  const [examName, setExamName] = useState("");
  const [classId, setClassId] = useState<string>("");
  const [classes, setClasses] = useState<ClassOption[]>([]);
  const [questions, setQuestions] = useState<Question[]>([
    { question_number: 1, question_text: "", expected_answer: "", max_score: 1.0 },
  ]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.get<ClassOption[]>('/classes').then(({ data }) => setClasses(data)).catch(() => {});
  }, []);

  const addQuestion = () => {
    setQuestions([
      ...questions,
      {
        question_number: questions.length + 1,
        question_text: "",
        expected_answer: "",
        max_score: 1.0,
      },
    ]);
  };

  const removeQuestion = (idx: number) => {
    const updated = questions.filter((_, i) => i !== idx).map((q, i) => ({ ...q, question_number: i + 1 }));
    setQuestions(updated);
  };

  const updateQuestion = (idx: number, field: keyof Question, value: string | number) => {
    const updated = [...questions];
    (updated[idx] as any)[field] = value;
    setQuestions(updated);
  };

  const handleSave = async () => {
    if (!examName.trim()) {
      setError("Digite o nome da prova.");
      return;
    }
    if (!classId) {
      setError("Selecione a turma.");
      return;
    }
    if (questions.some((q) => !q.question_text.trim() || !q.expected_answer.trim())) {
      setError("Preencha o enunciado e o gabarito de todas as questões.");
      return;
    }

    setSaving(true);
    setError(null);

    try {
      const payload: { name: string; class_id?: string; is_practical: boolean } = {
        name: examName.trim(),
        is_practical: isPractical,
      };
      if (classId) payload.class_id = classId;
      const { data: exam } = await api.post('/exams', payload);

      for (const q of questions) {
        await api.post(`/exams/${exam.id}/questions`, {
          question_number: q.question_number,
          question_text: q.question_text,
          expected_answer: q.expected_answer,
          max_score: q.max_score,
        });
      }

      router.push(isPractical ? '/provas-praticas' : '/exams');
    } catch {
      setError("Erro ao salvar a prova.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="max-w-4xl mx-auto space-y-6 animate-in fade-in duration-500">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">
          {isPractical ? 'Nova Prova Prática' : 'Nova Prova'}
        </h1>
        <p className="text-slate-500 mt-1">Cadastre o enunciado e o gabarito de cada questão.</p>
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
            <option value="">Selecione a turma...</option>
            {classes.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name} ({c.student_count} aluno{c.student_count !== 1 ? "s" : ""})
              </option>
            ))}
          </select>
          {classes.length === 0 && (
            <p className="text-xs text-amber-600 dark:text-amber-400 mt-1">
              Nenhuma turma cadastrada. Crie uma turma primeiro em &quot;Turmas &amp; CSV&quot;.
            </p>
          )}
        </div>
      </div>

      {questions.map((q, idx) => (
        <div key={idx} className="glass-panel rounded-xl p-6 border border-surface-border space-y-4">
          <div className="flex justify-between items-center">
            <h3 className="font-bold text-lg text-slate-800 dark:text-slate-200">
              Questão {q.question_number}
            </h3>
            <div className="flex items-center gap-3">
              <label className="text-sm text-slate-500">Peso:</label>
              <select
                value={q.max_score}
                onChange={(e) => updateQuestion(idx, "max_score", parseFloat(e.target.value))}
                className="bg-slate-50 dark:bg-slate-800/50 border border-slate-200 dark:border-slate-700 rounded-lg px-3 py-1.5 text-sm"
              >
                <option value={0.25}>0,25</option>
                <option value={0.5}>0,50</option>
                <option value={0.75}>0,75</option>
                <option value={1.0}>1,00</option>
              </select>
              {questions.length > 1 && (
                <button
                  type="button"
                  onClick={() => removeQuestion(idx)}
                  className="p-1.5 text-red-400 hover:text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20 rounded-lg transition-colors"
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              )}
            </div>
          </div>

          <div>
            <label className="block text-xs font-medium text-slate-500 mb-1">Enunciado</label>
            <textarea
              value={q.question_text}
              onChange={(e) => updateQuestion(idx, "question_text", e.target.value)}
              placeholder="Digite o enunciado da questão..."
              rows={2}
              className="w-full bg-slate-50 dark:bg-slate-800/50 border border-slate-200 dark:border-slate-700 rounded-lg px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/20 focus:border-emerald-500 resize-none"
            />
          </div>

          <div>
            <label className="block text-xs font-medium text-slate-500 mb-1">
              Padrão de Resposta (Gabarito)
            </label>
            <textarea
              value={q.expected_answer}
              onChange={(e) => updateQuestion(idx, "expected_answer", e.target.value)}
              placeholder="Digite aqui a resposta esperada completa que a IA usará como referência para corrigir..."
              rows={4}
              className="w-full bg-slate-50 dark:bg-slate-800/50 border border-slate-200 dark:border-slate-700 rounded-lg px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/20 focus:border-emerald-500 resize-none"
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
          <span>Salvar Prova</span>
        </button>
      </div>
    </div>
  );
}

export default function NewExamPage() {
  return <NewExamPageContent />;
}
