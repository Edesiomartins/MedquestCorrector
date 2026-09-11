"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Move, Plus, Trash2 } from "lucide-react";

export type DiscursiveLayoutPage = {
  page_index: number;
  width_pt: number;
  height_pt: number;
  preview_data_url: string;
};

export type DiscursiveLayoutQuestion = {
  question_number: number;
  page_index: number;
  question_text: string;
  x_pt: number;
  y_bottom_pt: number;
  width_pt: number;
  height_pt: number;
  confidence?: number | null;
  provenance?: string | null;
  answer_line_count?: number;
  expected_answer?: string;
  correction_criteria?: string | null;
  max_score?: number;
};

type DragState = {
  index: number;
  mode: "move" | "resize";
  startX: number;
  startY: number;
  startQuestion: DiscursiveLayoutQuestion;
  page: DiscursiveLayoutPage;
  rect: DOMRect;
};

type Props = {
  pages: DiscursiveLayoutPage[];
  questions: DiscursiveLayoutQuestion[];
  onChange: (questions: DiscursiveLayoutQuestion[]) => void;
};

function clamp(value: number, min: number, max: number) {
  return Math.min(Math.max(value, min), max);
}

function provenanceLabel(value?: string | null) {
  if (value === "answer_lines") return "linhas de resposta";
  if (value === "blank_space") return "área em branco";
  if (value === "manual") return "manual";
  return value || "detecção automática";
}

export default function DiscursiveLayoutEditor({ pages, questions, onChange }: Props) {
  const [selectedIndex, setSelectedIndex] = useState<number | null>(questions.length ? 0 : null);
  const [drag, setDrag] = useState<DragState | null>(null);
  const pageRefs = useRef<Record<number, HTMLDivElement | null>>({});

  useEffect(() => {
    if (!drag) return;

    const onPointerMove = (event: PointerEvent) => {
      event.preventDefault();
      const scaleX = drag.page.width_pt / Math.max(drag.rect.width, 1);
      const scaleY = drag.page.height_pt / Math.max(drag.rect.height, 1);
      const dx = (event.clientX - drag.startX) * scaleX;
      const dy = (event.clientY - drag.startY) * scaleY;
      const start = drag.startQuestion;
      const next = [...questions];

      if (drag.mode === "move") {
        const x = clamp(start.x_pt + dx, 0, drag.page.width_pt - start.width_pt);
        // Pointer Y cresce para baixo; y_bottom do manifesto cresce para cima.
        const yBottom = clamp(
          start.y_bottom_pt - dy,
          0,
          drag.page.height_pt - start.height_pt,
        );
        next[drag.index] = { ...start, x_pt: x, y_bottom_pt: yBottom, provenance: "manual" };
      } else {
        const visualTop = drag.page.height_pt - start.y_bottom_pt - start.height_pt;
        const width = clamp(start.width_pt + dx, 24, drag.page.width_pt - start.x_pt);
        const maxHeight = drag.page.height_pt - visualTop;
        const height = clamp(start.height_pt + dy, 24, maxHeight);
        const yBottom = clamp(drag.page.height_pt - visualTop - height, 0, drag.page.height_pt - height);
        next[drag.index] = {
          ...start,
          width_pt: width,
          height_pt: height,
          y_bottom_pt: yBottom,
          provenance: "manual",
        };
      }
      onChange(next);
    };

    const onPointerUp = () => setDrag(null);
    window.addEventListener("pointermove", onPointerMove, { passive: false });
    window.addEventListener("pointerup", onPointerUp);
    return () => {
      window.removeEventListener("pointermove", onPointerMove);
      window.removeEventListener("pointerup", onPointerUp);
    };
  }, [drag, onChange, questions]);

  const usedNumbers = useMemo(() => new Set(questions.map((item) => item.question_number)), [questions]);

  const addQuestion = (page: DiscursiveLayoutPage) => {
    let nextNumber = Math.max(0, ...questions.map((item) => item.question_number)) + 1;
    while (usedNumbers.has(nextNumber)) nextNumber += 1;
    const width = Math.max(80, page.width_pt - 84);
    const height = Math.min(130, Math.max(70, page.height_pt * 0.18));
    const item: DiscursiveLayoutQuestion = {
      question_number: nextNumber,
      page_index: page.page_index,
      question_text: `Questão ${nextNumber}`,
      x_pt: 42,
      y_bottom_pt: 42,
      width_pt: Math.min(width, page.width_pt - 42),
      height_pt: height,
      confidence: null,
      provenance: "manual",
      expected_answer: "",
      correction_criteria: "",
      max_score: 1,
    };
    const next = [...questions, item];
    onChange(next);
    setSelectedIndex(next.length - 1);
  };

  const updateQuestion = (index: number, patch: Partial<DiscursiveLayoutQuestion>) => {
    onChange(questions.map((item, current) => (current === index ? { ...item, ...patch } : item)));
  };

  const removeQuestion = (index: number) => {
    const next = questions.filter((_, current) => current !== index);
    onChange(next);
    setSelectedIndex(next.length ? Math.min(index, next.length - 1) : null);
  };

  const startDrag = (
    event: React.PointerEvent,
    index: number,
    page: DiscursiveLayoutPage,
    mode: "move" | "resize",
  ) => {
    event.preventDefault();
    event.stopPropagation();
    const container = pageRefs.current[page.page_index];
    if (!container) return;
    setSelectedIndex(index);
    setDrag({
      index,
      mode,
      startX: event.clientX,
      startY: event.clientY,
      startQuestion: questions[index],
      page,
      rect: container.getBoundingClientRect(),
    });
  };

  return (
    <div className="space-y-8">
      {pages.map((page) => {
        const pageQuestions = questions
          .map((question, index) => ({ question, index }))
          .filter(({ question }) => question.page_index === page.page_index);

        return (
          <section key={page.page_index} className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_320px]">
            <div>
              <div className="mb-2 flex items-center justify-between">
                <h3 className="font-semibold text-slate-800">Página {page.page_index + 1}</h3>
                <button
                  type="button"
                  onClick={() => addQuestion(page)}
                  className="inline-flex items-center gap-2 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-1.5 text-sm font-medium text-emerald-700 hover:bg-emerald-100"
                >
                  <Plus className="h-4 w-4" />
                  Adicionar questão
                </button>
              </div>

              <div
                ref={(element) => {
                  pageRefs.current[page.page_index] = element;
                }}
                className="relative mx-auto overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm"
                style={{ aspectRatio: `${page.width_pt} / ${page.height_pt}` }}
              >
                <img
                  src={page.preview_data_url}
                  alt={`Prévia da página ${page.page_index + 1}`}
                  className="absolute inset-0 h-full w-full select-none object-contain"
                  draggable={false}
                />

                {pageQuestions.map(({ question, index }) => {
                  const topPt = page.height_pt - question.y_bottom_pt - question.height_pt;
                  const selected = selectedIndex === index;
                  return (
                    <div
                      key={`${question.question_number}-${index}`}
                      role="button"
                      tabIndex={0}
                      aria-label={`Área da questão ${question.question_number}`}
                      onPointerDown={(event) => startDrag(event, index, page, "move")}
                      onClick={() => setSelectedIndex(index)}
                      className={`absolute cursor-move border-2 ${
                        selected
                          ? "border-emerald-600 bg-emerald-400/20"
                          : "border-amber-500 bg-amber-300/15"
                      }`}
                      style={{
                        left: `${(question.x_pt / page.width_pt) * 100}%`,
                        top: `${(topPt / page.height_pt) * 100}%`,
                        width: `${(question.width_pt / page.width_pt) * 100}%`,
                        height: `${(question.height_pt / page.height_pt) * 100}%`,
                      }}
                    >
                      <span className="absolute -top-6 left-0 rounded bg-slate-900 px-2 py-0.5 text-xs font-semibold text-white shadow">
                        Q{question.question_number}
                      </span>
                      <span className="absolute left-1 top-1 rounded bg-white/90 p-1 text-slate-700 shadow-sm">
                        <Move className="h-3.5 w-3.5" />
                      </span>
                      <button
                        type="button"
                        aria-label={`Redimensionar questão ${question.question_number}`}
                        onPointerDown={(event) => startDrag(event, index, page, "resize")}
                        className="absolute -bottom-2 -right-2 h-5 w-5 cursor-se-resize rounded-sm border-2 border-white bg-emerald-600 shadow"
                      />
                    </div>
                  );
                })}
              </div>
            </div>

            <div className="space-y-3 rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
              <div>
                <h4 className="font-semibold text-slate-800">Questões desta página</h4>
                <p className="mt-1 text-xs text-slate-500">Clique numa caixa para editar. Arraste para mover e use o canto inferior direito para redimensionar.</p>
              </div>

              {pageQuestions.length === 0 ? (
                <div className="rounded-lg border border-dashed border-amber-300 bg-amber-50 p-3 text-sm text-amber-800">
                  Nenhuma questão foi detectada nesta página. Adicione a área manualmente.
                </div>
              ) : (
                pageQuestions.map(({ question, index }) => (
                  <div
                    key={`form-${question.question_number}-${index}`}
                    className={`space-y-2 rounded-lg border p-3 ${selectedIndex === index ? "border-emerald-300 bg-emerald-50/50" : "border-slate-200"}`}
                    onClick={() => setSelectedIndex(index)}
                  >
                    <div className="flex items-center gap-2">
                      <label className="flex-1 text-xs font-medium text-slate-600">
                        Número
                        <input
                          type="number"
                          min={1}
                          value={question.question_number}
                          onChange={(event) => updateQuestion(index, { question_number: Number(event.target.value) || 0, provenance: "manual" })}
                          className="mt-1 w-full rounded-md border border-slate-300 bg-white px-2 py-1.5 text-sm"
                        />
                      </label>
                      <button
                        type="button"
                        onClick={(event) => {
                          event.stopPropagation();
                          removeQuestion(index);
                        }}
                        className="mt-5 rounded-md p-2 text-rose-600 hover:bg-rose-50"
                        aria-label={`Remover questão ${question.question_number}`}
                      >
                        <Trash2 className="h-4 w-4" />
                      </button>
                    </div>
                    <label className="block text-xs font-medium text-slate-600">
                      Enunciado detectado
                      <textarea
                        rows={4}
                        value={question.question_text}
                        onChange={(event) => updateQuestion(index, { question_text: event.target.value })}
                        className="mt-1 w-full resize-y rounded-md border border-slate-300 bg-white px-2 py-1.5 text-sm"
                      />
                    </label>
                    <label className="block text-xs font-medium text-slate-600">
                      Resposta esperada
                      <textarea
                        rows={3}
                        value={question.expected_answer || ""}
                        onChange={(event) => updateQuestion(index, { expected_answer: event.target.value })}
                        className="mt-1 w-full resize-y rounded-md border border-slate-300 bg-white px-2 py-1.5 text-sm"
                      />
                    </label>
                    {!(question.expected_answer || "").trim() ? (
                      <p className="text-xs font-medium text-amber-700">Gabarito ainda não informado</p>
                    ) : null}
                    <label className="block text-xs font-medium text-slate-600">
                      Critérios de correção
                      <textarea
                        rows={3}
                        value={question.correction_criteria || ""}
                        onChange={(event) => updateQuestion(index, { correction_criteria: event.target.value })}
                        className="mt-1 w-full resize-y rounded-md border border-slate-300 bg-white px-2 py-1.5 text-sm"
                      />
                    </label>
                    <label className="block text-xs font-medium text-slate-600">
                      Valor da questão
                      <input
                        type="number"
                        min={0.1}
                        step={0.1}
                        value={question.max_score ?? 1}
                        onChange={(event) =>
                          updateQuestion(index, { max_score: Number(event.target.value) || 1 })
                        }
                        className="mt-1 w-full rounded-md border border-slate-300 bg-white px-2 py-1.5 text-sm"
                      />
                    </label>
                    <div className="flex flex-wrap gap-2 text-xs text-slate-500">
                      <span>{provenanceLabel(question.provenance)}</span>
                      {question.confidence != null ? <span>• confiança {Math.round(question.confidence * 100)}%</span> : null}
                      {question.answer_line_count ? <span>• {question.answer_line_count} linhas</span> : null}
                    </div>
                  </div>
                ))
              )}
            </div>
          </section>
        );
      })}
    </div>
  );
}
