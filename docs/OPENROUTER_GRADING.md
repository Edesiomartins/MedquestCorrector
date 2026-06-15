# medquestcorrector - Estratégia de Correção via OpenRouter

Este documento define os padrões para uso da API OpenRouter para orquestração dos modelos de LLM.

## Abstração do Fornecedor

Não podemos acoplar o sistema à SDK de um único provedor como OpenAI ou Anthropic diretamente. O OpenRouter funciona como intermediário que homogeneíza os _payloads_.

```python
class GradingService:
    def __init__(self, llm_provider: LLMProvider):
        self.llm = llm_provider
    
    async def grade(self, context: GradingContext) -> GradingResult:
        # Orquestração do prompt...
        pass
```

## Perfis de Modelo no Sistema

1. **Modelo Corretor Principal:** `anthropic/claude-3.5-sonnet`
   - **Motivo:** Claude 3.5 Sonnet é atualmente superior na aderência rigorosa a rubricas (instructions following) do que o GPT-4o, e muito mais em conta.
   - **Responsabilidade:** Receber o JSON de contexto e devolver o `Structured Output`.
2. **Modelo Revisor (Segunda Opinião):** `openai/gpt-4o-mini`
   - **Motivo:** Uso apenas em incerteza, sendo baratíssimo.
3. **Modelo Visual Fallback:** `openai/gpt-4o`
   - **Motivo:** O multimodal da OpenAI ainda possui ligeira vantagem em leitura visual complexa. Usado quando o Azure OCR falhar e a flag `needs_fallback` estiver ativada.

## Protocolo de Comunicação e Prompting

### O Payload de Entrada (Prompt Builder)

A compilação que o `GradingService` entrega ao modelo será estruturada e objetiva. Nada de promts gigantes e confusos.

**System Prompt:**
> "Você é um avaliador experiente de avaliações discursivas universitárias. Sua função é receber o Enunciado, o Espelho da Resposta Esperada, as Regras de Avaliação (Rubrica), e a Transcrição (Texto do Aluno). Você deve analisar e preencher obrigatoriamente a estrutura JSON solicitada."

**User Prompt:**
```text
[ENUNCIADO]
{{question.text}}

[MÁXIMO DE PONTOS]
{{question.max_score}}

[ESPELHO/GABARITO ESPERADO]
{{question.expected_answer}}

[RUBRICA/CRITÉRIOS]
{{question.rubrics}} # Array contendo pesos e mandamentos

[RESPOSTA EXTRAÍDA DO ALUNO (OCR)]
{{ocr.extracted_text}}
```

### JSON Estruturado Esperado (Structured Output)

A API deve ser instruída (usando JSON Schema / function calling do OpenRouter) a retornar EXATAMENTE:

```json
{
  "score_suggested": 1.5,
  "max_score": 2.0,
  "criteria_met": [
    "Identificou o órgão", 
    "Mencionou a função principal"
  ],
  "criteria_missing": [
    "Não citou a complicação primária"
  ],
  "justification": "O aluno respondeu corretamente a primeira parte sobre as funções do órgão, mas em nenhum momento mencionou as complicações exigidas no critério 3.",
  "grading_confidence": 0.85,
  "manual_review_required": false,
  "manual_review_reason": null
}
```

### Regras de Gatilho para Revisão Obrigatória
O worker deve forçar a alteração de `manual_review_required = True` antes de salvar no banco, independentemente do que o LLM disser, caso alguma das condições abaixo ocorra:

1. O OCR detectou `<INLEGÍVEL>`.
2. A resposta do LLM na chave `grading_confidence` for inferior a 0.70.
3. O LLM zerar a nota, mas o comprimento da resposta do aluno for superior a 200 caracteres (risco de erro de interpretação punindo aluno que escreveu muito).
4. O parse do JSON retornado pela API do LLM falhar.
