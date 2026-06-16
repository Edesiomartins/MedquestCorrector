"use client";

import type { ReactNode } from "react";
import { AuthProvider } from "@/contexts/AuthContext";
import { ExamNavProvider } from "@/contexts/ExamNavContext";
import AppShell from "@/components/layout/AppShell";

export default function Providers({ children }: { children: ReactNode }) {
  return (
    <AuthProvider>
      <ExamNavProvider>
        <AppShell>{children}</AppShell>
      </ExamNavProvider>
    </AuthProvider>
  );
}
