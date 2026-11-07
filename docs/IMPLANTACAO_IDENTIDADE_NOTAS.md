# Implantação da correção de identidade, notas e leitura

Esta entrega não altera banco de produção, não faz deploy e não reprocessa lotes antigos.

## O que mudou

- Cada folha física permanece separada. O QR pode vincular o cadastro, mas a ordem do manifesto não escolhe o aluno.
- Nome genérico (`ALUNO 09`) não esconde o nome manuscrito. A confirmação do professor prevalece na exibição e na exportação.
- Questão repetida não substitui a anterior.
- A nota final é a soma dos pontos confirmados. Pendência não vira zero e não fecha a nota.
- Prova discursiva converte a fração (0, 0,25, 0,5, 0,75, 1) pelo valor da questão.
- Prova prática continua binária. Lateralidade exigida só quando a prova configura `laterality_mode: required`.
- Releitura discordante guarda a alternativa e mantém a primeira transcrição.
- Alterar a transcrição na revisão invalida a nota até o professor confirmar de novo.

## Migração

Arquivo: `backend/alembic/versions/20261103_sheet_identity.py`

Revisão: `20261103_sheet_identity`  
Anterior: `20260821_htr_labels`

Aplicar no ambiente de destino, fora de produção até a janela combinada:

```bash
cd backend
alembic upgrade 20261103_sheet_identity
```

A migração só adiciona colunas nulas:

- `exams.grading_policy_json`
- `student_results.handwritten_name`
- `student_results.confirmed_name`
- `visual_exam_answers.original_transcription`
- `visual_exam_answers.handwritten_name`
- `visual_exam_answers.registered_name`
- `visual_exam_answers.confirmed_name`
- `visual_exam_answers.identity_source`

## Política da prova prática deste gabarito

Para exigir D/E quando o gabarito traz lado, grave na prova:

```json
{"laterality_mode": "required"}
```

no campo `grading_policy_json`. Sem esse JSON, a omissão de lado continua tolerada, como nas provas já corrigidas.

## Leitura remota de cabeçalho no worker

`HEADER_REMOTE_READ_ENABLED` fica desligada. Ligar faz uma chamada de visão por página e gera custo. O fluxo visual das manuscritas já lê o cabeçalho quando o cadastro é genérico ou o QR não resolve.

## Rollback

```bash
cd backend
alembic downgrade 20260821_htr_labels
```

O downgrade remove as colunas novas. Nomes confirmados gravados nelas deixam de existir. O código antigo volta a usar o manifesto como identidade e a média com arredondamento em quartos. Não rode o downgrade se o ambiente já depende das colunas.

## Execuções antigas

Não são regravadas. Exportar de novo um lote visual antigo separa folhas que tinham a mesma questão repetida, mas não recupera um nome manuscrito que não foi lido. Para aplicar a identidade nova, reprocesse o PDF em um lote novo, com a migração já aplicada. Exporte o lote antigo antes, se houver revisão humana: o reprocessamento do worker ainda apaga resultados automáticos daquele lote.

## Validação que não foi feita

Os cinco PDFs e as planilhas de referência não estavam no ambiente. Não há medida de precisão contra essas provas. Não tratar concordância com a planilha do ChatGPT como correção confirmada: na rasura em que o texto final é outro músculo, a referência externa pode estar errada.
