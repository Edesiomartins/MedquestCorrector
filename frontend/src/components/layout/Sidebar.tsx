"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Home, FileText, CheckSquare, Users, ScanText, ClipboardList, History } from "lucide-react";

const active =
  "flex items-center space-x-3 px-3 py-2.5 rounded-lg bg-emerald-50 dark:bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 font-medium";
const inactive =
  "flex items-center space-x-3 px-3 py-2.5 rounded-lg text-slate-600 dark:text-slate-400 hover:bg-slate-50 dark:hover:bg-slate-800/50 transition-colors";

function linkClass(pathname: string, href: string) {
  if (href === "/") return pathname === "/" ? active : inactive;

  if (href === "/exams") {
    const isDiscursive =
      pathname === "/exams" ||
      pathname === "/exams/new" ||
      /^\/exams\/[^/]+\/edit$/.test(pathname);
    return isDiscursive ? active : inactive;
  }

  if (href === "/provas-praticas") {
    const isPractical =
      pathname === "/provas-praticas" ||
      pathname === "/provas-praticas/new" ||
      /^\/provas-praticas\/[^/]+\/edit$/.test(pathname);
    return isPractical ? active : inactive;
  }

  return pathname === href || pathname.startsWith(`${href}/`) ? active : inactive;
}

export default function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="w-64 h-screen glass-panel flex flex-col fixed left-0 top-0 border-r border-surface-border">
      <div className="px-4 py-5 border-b border-surface-border/60 flex items-center justify-center">
        <img
          src="/medquestcorrector-logo.png"
          alt="medquestcorrector"
          className="w-48 h-auto object-contain"
        />
      </div>

      <nav className="flex-1 px-4 space-y-1 mt-4">
        <Link href="/" className={linkClass(pathname, "/")}>
          <Home className="w-5 h-5" />
          <span>Dashboard</span>
        </Link>
        <Link href="/classes" className={linkClass(pathname, "/classes")}>
          <Users className="w-5 h-5" />
          <span>Turmas</span>
        </Link>
        <Link href="/exams" className={linkClass(pathname, "/exams")}>
          <FileText className="w-5 h-5" />
          <span>Provas Discursivas</span>
        </Link>
        <Link href="/provas-praticas" className={linkClass(pathname, "/provas-praticas")}>
          <ClipboardList className="w-5 h-5" />
          <span>Provas Práticas</span>
        </Link>
        <Link href="/review" className={linkClass(pathname, "/review")}>
          <CheckSquare className="w-5 h-5" />
          <span>Revisão Pendente</span>
        </Link>
        <Link href="/historico" className={linkClass(pathname, "/historico")}>
          <History className="w-5 h-5" />
          <span>Histórico</span>
        </Link>
        <Link href="/manuscritas" className={linkClass(pathname, "/manuscritas")}>
          <ScanText className="w-5 h-5" />
          <span>Prova manuscrita</span>
        </Link>
      </nav>
    </aside>
  );
}
