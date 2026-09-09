import pymupdf as fitz
from PIL import Image

class PDFParserService:
    @staticmethod
    def extract_pages_as_images(pdf_bytes: bytes, dpi: int = 300) -> list[Image.Image]:
        """
        Extrai todas as páginas de um PDF em formato binário e as converte para instâncias de PIL.Image.
        O DPI padrão é 300 para garantir que o recorte mantenha alta qualidade para o OCR manuscrito.
        """
        # Abre o PDF a partir de um stream em memória
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        images = []
        
        # O fator de zoom calcula o multiplicador de DPI (72 é o padrão base do PyMuPDF)
        zoom = dpi / 72.0
        mat = fitz.Matrix(zoom, zoom)
        
        for page_num in range(len(doc)):
            page = doc.load_page(page_num)
            # Renderiza a página
            pix = page.get_pixmap(matrix=mat, alpha=False)
            
            # Converte o pixmap gerado (C++) nativo do MuPDF para objeto do Pillow (Python)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            images.append(img)
            
        doc.close()
        return images
