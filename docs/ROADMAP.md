# medquestcorrector - Roadmap Estratégico

Este documento foca na execução pragmática do produto visando entregar valor em produção rapidamente (Mentalidade Low Cost e MVP), escalando as funcionalidades à medida que o projeto ganha tração.

## Premissas de Produto

- **Não perderemos tempo criando um alinhador de PDF genérico.** A plataforma funcionará com PDF de base gerado por ela mesma.
- **A IA atua como co-piloto.** Nenhuma nota vai direto ao diário/consolidado sem a revisão rápida de um humano usando a interface de auditoria.

---

## Fase 1: Arquitetura e PoC (Prova de Conceito) - MVP Inicial
**Foco:** Testar o end-to-end com um caso ideal.
- **Backend/DB:** Setup do FastAPI, PostgreSQL (Modelos Base).
- **PDF Gen:** Criação de um gerador de template (código simples) injetando QR Codes nos cantos para Deskew e caixas de bounding box visíveis e mapeadas.
- **Worker 1 (Visão):** Script simples em Python (OpenCV) que corta as regiões da imagem com base num layout hardcoded de PoC.
- **OCR e LLM:** Implementação bruta da conexão ao `Azure Document Intelligence` e chamada OpenRouter para o `Claude 3.5 Sonnet`.
- **Validação:** Um script que roda todo o processo, pega a resposta do Sonnet e joga no console. Sem UI neste momento.

## Fase 2: Plataformização Funcional (MVP para Professores)
**Foco:** Entregar nas mãos dos primeiros professores e criar as interfaces.
- **Frontend Core:** Autenticação e painel de criação do Exame e das Rubricas no Next.js.
- **Pipeline Assíncrono:** Instalar Celery + Redis. Criar status de tracking (PENDING, PROCESSING, DONE). Tela do frontend fazendo pooling de progresso.
- **UI de Revisão Ligeira:** A tela mais importante do sistema. Exibir side-by-side imagem do aluno vs. justificativa e input pra alterar nota e aprovar. Atalhos de teclado (Enter = Aprova, Seta Direito = Próxima).
- **Exportador Mínimo:** Botão "Baixar Notas (.csv)" ao finalizar as auditorias.

## Fase 3: Beta Feedback (Trabalhando a Otimização)
**Foco:** Ajustar o modelo base com base em uso de professores reais em ambiente escolar.
- **Visão Avançada:** Melhorar a resiliência a amassados no papel (Perspective Transform usando os QR codes).
- **Fallback Visual:** Ativar a camada de `VisualFallbackService` descrita na arquitetura para corrigir o gargalo do OCR em caligrafias péssimas.
- **Prompt Engineering Tuning:** Coletar logs de onde a IA errou e ajustar o `system_prompt` de acordo com o padrão das respostas dadas pelo professor na UI de Revisão Ligeira.
- **Relatórios:** Gráficos no Next.js (Média, Dificuldade da Questão - baseado na % de acerto, tempo de correção poupado).

## Fase 4: Produção Escalável e Monetização (Futuro)
**Foco:** SaaS / SaaS B2B, transformar dores em receita.
- **Multi-Tenant e Hierarquia Institucional:** Perfis Admin que criam contas de professores, atrelados ao limite de uso do Colégio.
- **Billing e Walllet:** Sistema de Créditos. 1 Crédito = 1 Prova Corrigida. Integração com Stripe (Pagamentos) e controle de ledger (subtração das cotas a cada PDF inserido).
- **Integração Externa:** APIs e Webhooks para lançar as notas em sistemas acadêmicos externos.
- **Suporte a PDFs Dupla Face.**
