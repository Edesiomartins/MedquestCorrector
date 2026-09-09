# Benchmark de modelos VLM para leitura de manuscrito

Ferramenta: `backend/scripts/benchmark_htr_models.py`.

## Objetivo

Responder a uma pergunta só: **qual modelo de visão lê melhor as respostas
manuscritas deste projeto?**

`scripts/eval_htr.py` mede UMA configuração. Este script compara VÁRIAS, sobre
exatamente os mesmos recortes e as mesmas referências humanas. Nada aqui toca o
pipeline de produção: o benchmark não muda `OPENROUTER_VISION_MODEL`, não altera
a correção de provas e não faz deploy.

Três decisões sustentam a validade do experimento — mexer nelas invalida a
comparação:

1. **Sem fallback.** Cada modelo é chamado com
   `transcribe_answer_crop(..., allow_fallback=False)`. Se o modelo A falhar e a
   cadeia caísse para o B, a tabela registraria o resultado do B na linha do A.
   Modelo que falha aparece como falha (`api_failures`), não como resultado.
2. **Sem detector de tinta, por padrão.** Em produção `detect_ink()` resolve
   caixa vazia antes de gastar chamada. No benchmark isso zeraria a alucinação
   de **todos** os modelos — que é justamente o primeiro critério de ordenação.
   As caixas vazias vão ao modelo de propósito. `--use-ink-detector` reproduz o
   caminho de produção quando o que se quer medir é o *pipeline*, não o modelo.
3. **Mesmo prompt para todos.** `ANSWER_TRANSCRIPTION_PROMPT` não é alterado.
   Mudar modelo e prompt ao mesmo tempo torna impossível atribuir a diferença a
   um dos dois. Comparação de prompts é um experimento separado.

A transcrição continua **cega**: a referência humana vai para o arquivo de
resultado, nunca para o prompt. Há teste automatizado para isso.

## Exportando o dataset pelo MedquestCorrector

O caminho mais curto para ter um dataset: o professor revisa as provas
normalmente e o sistema entrega o conjunto pronto, com as imagens junto.

```
GET /reviews/htr-dataset-bundle?exam_id=<uuid>&limit=1000
```

Autenticado (Bearer, como o resto de `/reviews`). Devolve
`medquest_htr_dataset_<exam_id>.zip`:

```
labels.jsonl
manifest.json
crops/
    crop_000001.png
    crop_000002.png
    ...
```

```bash
curl -H "Authorization: Bearer $TOKEN" \
     "$API/reviews/htr-dataset-bundle?exam_id=$EXAM_ID" \
     -o bundle.zip
unzip bundle.zip -d eval/prova-x

# roda direto, sem --crops: os caminhos ja sao relativos ao proprio bundle
python scripts/benchmark_htr_models.py \
    --labels eval/prova-x/labels.jsonl \
    --models "google/gemma-4-31b-it:free" "qwen/qwen3-vl-32b-instruct" \
    --dry-run
```

Os `crop` dentro do `labels.jsonl` são relativos (`crops/crop_000001.png`), então
o bundle roda em qualquer máquina, sem `--crops` — descompacte e aponte o
`--labels` para dentro dele.

### O que o bundle contém — e o que não contém

Contém, por linha: `crop`, `reference`, `question`, `strata`,
`model_transcription`, `cer_at_review`. Nada mais. A cópia é feita por **lista
branca** de campos: nome, matrícula, e-mail, `student_id`, cabeçalho da prova,
gabarito, `expected_answer` e critério de correção não têm por onde entrar — nem
hoje, nem quando alguém acrescentar um campo novo ao export.

Os nomes de arquivo são sequenciais (`crop_000001.png`), nunca derivados da
resposta ou do aluno: o nome de um arquivo aparece na listagem do ZIP.

`manifest.json`:

```json
{
  "exam_id": "…",
  "samples_exported": 187,
  "missing_crops": 3,
  "rejected_paths": 0,
  "empty_references": 24,
  "created_at": "2026-09-09T15:20:00+00:00"
}
```

- `missing_crops` — rótulos ignorados porque o PNG não estava no disco. A
  exportação não para por causa deles; se esse número for alto, os recortes do
  lote foram limpos e o conjunto ficou menor do que parece.
- `rejected_paths` — subconjunto do anterior: caminhos que a resolução segura
  recusou (fora da área de upload, absolutos, ou registros antigos no formato
  `batch=…/page=…`). Diferente de zero merece um olhar no log.
- `empty_references` — caixas em branco. Mire em ~15% do conjunto: são elas que
  medem alucinação.

### Limites desta versão

- **`exam_id` é obrigatório.** Não existe exportação global de todos os recortes
  do sistema num arquivo só; é uma superfície de vazamento que esta etapa não
  abre. Para juntar várias provas, exporte um bundle por prova e concatene os
  `labels.jsonl` você mesmo, renomeando os crops.
- O endpoint antigo `GET /reviews/htr-dataset` continua igual, devolvendo só o
  `labels.jsonl` com caminhos internos do servidor.
- O `reference` do bundle é a transcrição do professor feita na revisão. Ela é
  boa referência para CER, mas não passou por uma segunda leitura — para o
  conjunto "oficial" de avaliação, ver [HTR_EVAL_SET.md](HTR_EVAL_SET.md).

## Como preparar o dataset

O formato é o mesmo do arnês de avaliação — ver
[HTR_EVAL_SET.md](HTR_EVAL_SET.md), que descreve o que coletar e como
transcrever à mão. Em resumo: um diretório com os PNGs e um `labels.jsonl`, uma
linha por recorte:

```json
{"crop": "p001_q01.png", "reference": "actina e miosina deslizam", "strata": ["cursiva_ligada", "caneta_azul", "scanner"], "question": 1}
{"crop": "p001_q03.png", "reference": "", "strata": ["vazia", "scanner"], "question": 3}
```

- `reference` é a transcrição **humana**, exatamente como o aluno escreveu.
- `strata` são os eixos de estratificação (ver abaixo). Opcional, mas é o que
  revela em qual bolsão o modelo falha.
- `question` é opcional; quando presente, entra no prompt como "esta imagem é a
  resposta da questão N", igual à produção.

Cerca de **15% do conjunto deve ser caixa vazia** (`"reference": ""`). São elas
que medem alucinação, o erro mais grave do sistema.

Boa parte disso sai pronta da revisão do professor: o endpoint de exportação do
dataset HTR (`app/services/htr_labeling.export_dataset`) já devolve linhas neste
formato, com os estratos `confirmada` / `corrigida` / `vazia` / `curta`.

O script **falha alto** se algum recorte listado no `labels.jsonl` não existir no
diretório: conjunto parcial não é comparável com execução anterior.

## Como executar

```bash
cd backend

# 1) veja o custo antes de gastar: nenhuma chamada é feita
python scripts/benchmark_htr_models.py \
    --labels eval/labels.jsonl --crops eval/crops \
    --models "google/gemma-4-31b-it:free" "qwen/qwen3-vl-32b-instruct" \
    --dry-run

# 2) ensaio barato com os 10 primeiros recortes
python scripts/benchmark_htr_models.py \
    --labels eval/labels.jsonl --crops eval/crops --limit 10 \
    --models "google/gemma-4-31b-it:free" "qwen/qwen3-vl-32b-instruct"

# 3) bateria completa
python scripts/benchmark_htr_models.py \
    --labels eval/labels.jsonl \
    --crops eval/crops \
    --models "google/gemma-4-31b-it:free" \
             "qwen/qwen3-vl-32b-instruct" \
             "google/gemini-2.5-flash"
```

Requer `OPENROUTER_API_KEY` no ambiente. **Custa `nº de recortes × nº de
modelos` chamadas de LLM** — o script imprime esse total antes de começar e pede
confirmação em terminal interativo (`--yes` pula, `--dry-run` só mostra).
Comece sempre pelo `--limit`.

| Flag | Para quê |
| --- | --- |
| `--labels` | `labels.jsonl` com as referências humanas (obrigatório) |
| `--crops` | diretório dos recortes (default: ao lado do `--labels`) |
| `--models` | um ou mais ids de modelo do OpenRouter (obrigatório) |
| `--results-dir` | saída (default: `<dir do labels>/results`) |
| `--limit N` | usa só os N primeiros recortes |
| `--dry-run` | mostra o custo em chamadas e sai |
| `--use-ink-detector` | mede o pipeline em vez do modelo (zera a alucinação) |
| `--yes` | não pede confirmação |

### Saídas

```
eval/results/
  gemma-4-31b-it.jsonl        # uma linha por recorte, por modelo
  qwen3-vl-32b-instruct.jsonl
  gemini-2.5-flash.jsonl
  benchmark_summary.json      # métricas completas + ranking + por estrato
  benchmark_summary.csv       # a mesma tabela, para planilha
```

Cada linha dos `.jsonl` é auditável recorte a recorte:

```json
{"crop": "p001_q01.png", "reference": "actina e miosina", "hypothesis": "actina e miosino",
 "confidence": "alta", "model": "qwen/qwen3-vl-32b-instruct", "elapsed_seconds": 2.71, "error": null}
```

`error` preenchido significa que **aquele modelo** falhou naquele recorte (a
hipótese fica vazia e conta como resposta não lida). A bateria continua: nem um
recorte ruim, nem um modelo fora do ar interrompem os demais.

Colunas do CSV: `model`, `samples`, `cer`, `cer_no_accents`, `wer`,
`perfect_reads`, `perfect_read_rate`, `missed_answers`, `empty_boxes`,
`hallucinated_empty`, `hallucination_rate`, `avg_latency_seconds`,
`api_failures`, `confidence_cer_correlation`.

## Como interpretar as métricas

Todas vêm de `app/services/vision/htr_metrics.py` — as mesmas da avaliação de
rotina, para que os números sejam comparáveis entre as duas ferramentas.

| Métrica | O que significa |
| --- | --- |
| **CER** | distância de edição ÷ tamanho da referência, por caractere. **É a métrica que importa.** Prediz se o professor vai precisar corrigir a transcrição. 0,05 = um caractere errado a cada vinte. |
| **CER sem acentos** | o mesmo ignorando acentuação. Diferença grande entre as duas = o modelo lê as letras mas não os acentos; é um problema menor e de outra natureza. |
| **WER** | erro por palavra. Menos útil aqui: errar um acento em "contração" e errar a palavra inteira custam a mesma WER. Serve como leitura secundária. |
| **leituras perfeitas / %** | recortes com resposta lidos com CER 0. O percentual usa como denominador só os recortes **com** resposta — caixa vazia não infla a taxa. |
| **respostas não lidas** | havia resposta e o modelo devolveu vazio (ou falhou). Custa nota zero indevida ao aluno. |
| **caixas vazias** | quantas referências estão em branco (tamanho do denominador da alucinação). |
| **alucinação em vazia** | o modelo escreveu algo onde o aluno não escreveu nada. **É o erro mais grave**: vira nota atribuída a resposta inexistente. |
| **taxa de alucinação** | alucinações ÷ caixas vazias. |
| **tempo médio por recorte** | latência média. Informativa; **não** entra na ordenação. |
| **falhas de API** | chamadas que erraram (HTTP, timeout, modelo fora do ar). Muitas falhas tornam as outras métricas daquele modelo pouco confiáveis — releia com o `samples` efetivo em mente. |
| **confiança × CER** | correlação de postos. Espera-se **negativa**. Perto de zero significa que a autoconfiança do modelo não carrega informação, e o gate de revisão manual baseado nela não significa nada. |

### Ordem do ranking

1. menor **taxa de alucinação**
2. menor **CER**
3. maior **percentual de leituras perfeitas**
4. menor **número de respostas não lidas**

Velocidade **não decide**. Um modelo rápido que inventa resposta em caixa vazia
custa mais caro que um lento que lê direito: a nota errada chega ao aluno.

### Por estrato

A média global esconde bolsões — as falhas de HTR se concentram em lápis fraco,
cursiva ligada e foto de celular. O script imprime, e o JSON guarda, as métricas
por estrato presentes no `labels.jsonl`. Eixos sugeridos:

`cursiva_ligada` · `bastao` · `mista` · `lapis` · `caneta_azul` · `caneta_preta`
· `scanner` · `celular` · `prova_pratica` · `prova_discursiva` · `vazia` ·
`curta`

Um modelo com CER global melhor mas CER muito pior em `cursiva_ligada` pode ser
a escolha errada, dependendo de como são as turmas.

## Como adicionar outro modelo

Acrescente o id do OpenRouter em `--models`. Nada mais precisa mudar:

```bash
python scripts/benchmark_htr_models.py --labels eval/labels.jsonl --crops eval/crops \
    --models "google/gemma-4-31b-it:free" "meta-llama/llama-4-maverick"
```

O nome do arquivo de saída é derivado do id (`google/gemma-4-31b-it:free` →
`gemma-4-31b-it.jsonl`), com desambiguação automática se dois ids gerarem o
mesmo nome. O modelo precisa ter visão — um modelo só de texto simplesmente
falhará em todos os recortes e aparecerá com `api_failures` igual ao tamanho do
conjunto, que é a informação correta.

## Como comparar resultados

- **Entre modelos, na mesma execução:** a tabela impressa e o
  `benchmark_summary.csv`, já ordenados pelo critério acima.
- **Entre execuções (mesmo modelo, momentos diferentes):** guarde o
  `benchmark_summary.json` de cada rodada. Ele registra `generated_at` e o
  `dataset` usado; comparar rodadas com conjuntos diferentes não significa nada.
- **Recorte a recorte:** os `.jsonl` por modelo têm a mesma ordem de `crop`, o
  que permite um `diff` direto para achar onde dois modelos discordam.
- **Contra a avaliação de rotina:** um `.jsonl` do benchmark é aceito pelo
  `eval_htr.py` como `--predictions`, o que dá o relatório completo (inclusive a
  calibração da confiança) e a comparação antes/depois:

```bash
python scripts/eval_htr.py --labels eval/labels.jsonl \
    --predictions eval/results/gemma-4-31b-it.jsonl \
    --compare eval/results/qwen3-vl-32b-instruct.jsonl
```

## Trocar o modelo de produção

O benchmark **não** troca nada. Se um modelo vencer, a mudança é manual e
consciente: `OPENROUTER_VISION_MODEL` (e eventualmente
`OPENROUTER_VISION_FALLBACKS`) no ambiente. Antes disso, olhe também
`api_failures` e as métricas por estrato — um vencedor na média que falha 20%
das chamadas não é um vencedor.

## Testes

`backend/tests/test_benchmark_htr_models.py` fixa as invariantes que separam
"medimos modelos" de "medimos outra coisa": mesmos recortes para todos, o modelo
pedido é o chamado, sem fallback silencioso, falha de um modelo não derruba a
bateria, métricas corretas e referência humana nunca no prompt.

```bash
cd backend && python -m pytest tests/test_benchmark_htr_models.py tests/test_answer_crop_transcription.py -q
```
