"use client";

import { createContext, useContext, useMemo, useState, type ReactNode } from "react";

export type ExamNavSection = "discursive" | "practical" | null;

type ExamNavContextValue = {
  section: ExamNavSection;
  setSection: (section: ExamNavSection) => void;
};

const ExamNavContext = createContext<ExamNavContextValue | null>(null);

export function ExamNavProvider({ children }: { children: ReactNode }) {
  const [section, setSection] = useState<ExamNavSection>(null);
  const value = useMemo(() => ({ section, setSection }), [section]);
  return <ExamNavContext.Provider value={value}>{children}</ExamNavContext.Provider>;
}

export function useExamNav() {
  const ctx = useContext(ExamNavContext);
  if (!ctx) {
    throw new Error("useExamNav must be used within ExamNavProvider");
  }
  return ctx;
}
