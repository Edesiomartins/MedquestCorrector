# medquestcorrector - Estratégia de OCR Manuscrito

A leitura de texto manuscrito/cursivo é um dos desafios mais complexos em visão computacional e frequentemente o ponto de estrangulamento para o sucesso da correção automática.

## Comparação de Provedores

1. **Azure Document Intelligence (antigo Form Recognizer - Read API v3.2+)**
   - **Prós:** Trata caligrafias cursivas da língua portuguesa de forma excepcional. Ele preserva a ordem de leitura em blocos contínuos. Possui o modelo Prebuilt Read que é muito eficiente.
   - **Contras:** Os metadados de confidence (nível de confiança por palavra) na API v3 dependem da análise do JSON resultante e podem não ser explícitos em todas as versões (exige parse minucioso de `words`).
   - **Custo:** Extremamente baixo (~$1.50 a cada 1.000 páginas).
   - **Veredito:** Melhor escolha para o MVP.

2. **Amazon Textract**
   - **Prós:** SDK unificado AWS, detecção nativa de manuscritos (Feature `DetectDocumentText`). Muito maduro.
   - **Contras:** Fica confuso mais rapidamente com letras excessivamente garrafais misturadas com cursivas ou linhas sobrepostas.
   - **Custo:** Similar ao Azure (cerca de $1.50/1k).

3. **Mistral OCR / Modelos Vision LLM (OpenAI GPT-4o)**
   - **Prós:** Eles aplicam "dedução semântica" se a letra for feia, acertando o contexto da frase melhor do que um OCR puro determinístico.
   - **Contras:** Absurdamente mais caros (cobrados por token ou por megapixel de imagem).
   - **Veredito:** Excelente para uso apenas como Fallback.

## Arquitetura de OCR (Abstração)

A arquitetura não dependerá do SDK direto em todos os lugares, mas de uma abstração no backend:

```python
from pydantic import BaseModel
from abc import ABC, abstractmethod

class OCRResult(BaseModel):
    text: str
    confidence_avg: float
    needs_fallback: bool

class OCRProvider(ABC):
    @abstractmethod
    async def extract_handwriting(self, image_url: str) -> OCRResult:
        pass
```

## Regras e Fluxo de Fallback

Dado um arquivo `.jpg` do box correspondente à resposta do aluno:

1. A imagem é enviada para o `AzureOCRProvider` (Principal).
2. O provedor converte para texto.
3. Se a quantidade de palavras extraídas for estranhamente baixa (em relação ao tamanho do crop), ou a média de `confidence` calculada pelas _bounding_boxes_ das palavras for **menor que 0.70**, a _flag_ `needs_fallback` torna-se `True`.
4. Se `needs_fallback == True`, a imagem é repassada para o `VisualFallbackService` (OpenRouter GPT-4o).
5. O modelo multimodal recebe a imagem original cropada e o texto do enunciado como contexto. O modelo lê a imagem diretamente:
   *Prompt:* `"Transcreva exatamente o que está escrito à mão nesta imagem. Não avalie ou critique a resposta, apenas transcreva. Se estiver completamente ininteligível, retorne '<<INLEGÍVEL>>'."`
6. O texto retornado pela engine (via fallback ou principal) é salvo no banco e o Worker prossegue para a etapa de Correção.
