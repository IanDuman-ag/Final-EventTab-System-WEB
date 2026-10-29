"""Fixed-template PDFs and bounded, local-only OCR. No external OCR calls."""
import io
import re
import time
import os
import shutil
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from PIL import Image, ImageOps, UnidentifiedImageError

from .ocr_service import OCRSheetError, fingerprint

WIDTH, HEIGHT = 842, 595
MAX_BYTES, MAX_PAGES, MAX_PIXELS = 10 * 1024 * 1024, 12, 20_000_000


def pages(snapshot):
    result = []
    for start in range(0, len(snapshot['participants']), 14):
        for col_start in range(0, len(snapshot['columns']), 6):
            participants = snapshot['participants'][start:start + 14]
            columns = snapshot['columns'][col_start:col_start + 6]
            cells = []
            cell_width = 540 / len(columns)
            for r, participant in enumerate(participants):
                for c, column in enumerate(columns):
                    cells.append({'key': f'{participant["id"]}/{column["key"]}',
                                  'box': [280 + c * cell_width, 165 + r * 23, cell_width, 23]})
            result.append({'participants': participants, 'columns': columns, 'cells': cells})
    if len(result) > MAX_PAGES:
        raise OCRSheetError('This printable sheet exceeds the 12-page upload limit.')
    return result


def identifier(sheet, page):
    return f'ET-OCR:{sheet.pk}:{page}:{fingerprint(sheet.snapshot)[:16]}'


def printable(sheet):
    from reportlab.pdfgen import canvas
    from reportlab.graphics.barcode import qr
    from reportlab.graphics.shapes import Drawing
    from reportlab.graphics import renderPDF
    from reportlab.lib.utils import simpleSplit
    out = io.BytesIO()
    pdf = canvas.Canvas(out, pagesize=(WIDTH, HEIGHT))
    for index, page in enumerate(pages(sheet.snapshot)):
        pdf.setLineWidth(2)
        pdf.rect(18, 18, WIDTH - 36, HEIGHT - 36)
        code = qr.QrCodeWidget(identifier(sheet, index))
        bounds = code.getBounds(); size = bounds[2] - bounds[0]
        drawing = Drawing(112, 112, transform=[112 / size, 0, 0, 112 / size, 0, 0]); drawing.add(code)
        renderPDF.draw(drawing, pdf, 28, HEIGHT - 144)
        pdf.setFont('Helvetica-Bold', 15); pdf.drawString(165, HEIGHT - 48, 'EventTab · Judge Score Sheet')
        pdf.setFont('Helvetica', 10)
        for y, text in [(67, sheet.snapshot['event']), (84, sheet.snapshot['round']), (101, 'Judge: ' + sheet.snapshot['judge'])]:
            pdf.drawString(165, HEIGHT - y, text[:100])
        pdf.setFont('Helvetica', 7)
        pdf.drawString(165, HEIGHT - 130, f'Sheet {sheet.pk} · Page {index + 1} · Keep the border and QR visible.')
        pdf.drawString(30, HEIGHT - 151, 'PARTICIPANT / ID')
        cw = 540 / len(page['columns'])
        for c, column in enumerate(page['columns']):
            for line, text in enumerate(simpleSplit(f'{column["name"]} ({column["maximum"]})', 'Helvetica', 7, cw - 8)[:3]):
                pdf.drawString(284 + c * cw, HEIGHT - 141 - line * 8, text)
        for r, participant in enumerate(page['participants']):
            y = 165 + r * 23
            pdf.setFont('Helvetica', 8)
            pdf.drawString(30, HEIGHT - y - 15, f'{participant["id"]} · {participant["name"]}'[:57])
        pdf.setLineWidth(.6)
        for cell in page['cells']:
            x, y, w, h = cell['box']; pdf.rect(x, HEIGHT - y - h, w, h)
        pdf.setFont('Helvetica', 9)
        pdf.drawString(30, 55, 'Judge signature: ____________________________   Date: __________________')
        pdf.showPage()
    pdf.save()
    return out.getvalue()


def load_images(raw, *, verify_only=False):
    if not raw or len(raw) > MAX_BYTES:
        raise OCRSheetError('Upload a JPG, PNG, or PDF no larger than 10 MB.')
    if raw.startswith(b'%PDF-'):
        try:
            import pypdfium2 as pdfium
            document = pdfium.PdfDocument(raw)
            try:
                if not 1 <= len(document) <= MAX_PAGES:
                    raise OCRSheetError('PDFs must contain 1–12 pages.')
                images = []
                total_pixels = 0
                for i in range(len(document)):
                    page = document[i]
                    try:
                        width, height = page.get_size()
                        total_pixels += width * height * 4
                        if width * height * 4 > MAX_PIXELS or total_pixels > 40_000_000:
                            raise OCRSheetError('A PDF page is too large to process safely.')
                        if not verify_only:
                            bitmap = page.render(scale=2)
                            try:
                                images.append(bitmap.to_pil().convert('RGB').copy())
                            finally:
                                bitmap.close()
                    finally:
                        page.close()
                return images, 'application/pdf'
            finally:
                document.close()
        except OCRSheetError:
            raise
        except Exception as exc:
            raise OCRSheetError('The PDF is invalid, encrypted, or the local PDF renderer is unavailable.') from exc
    try:
        with Image.open(io.BytesIO(raw)) as image:
            if image.format not in ('JPEG', 'PNG') or image.width * image.height > MAX_PIXELS or getattr(image, 'n_frames', 1) != 1:
                raise OCRSheetError('Use a single JPG/PNG image under 20 megapixels, or a scanned PDF.')
            image.verify()
        with Image.open(io.BytesIO(raw)) as image:
            result = ImageOps.exif_transpose(image).convert('RGB')
        return [] if verify_only else [result], 'image/png' if raw.startswith(b'\x89PNG') else 'image/jpeg'
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise OCRSheetError('The upload is not a valid JPG, PNG, or PDF.') from exc


def check_identifiers(sheet, raw):
    """Unknown paper layouts permit manual entry; known mismatched IDs never do."""
    import cv2
    import numpy as np
    images, _ = load_images(raw)
    seen = set()
    for image in images:
        text, _, _ = cv2.QRCodeDetector().detectAndDecode(np.array(image))
        if not text.startswith('ET-OCR:'):
            continue
        parts = text.split(':')
        if len(parts) != 4 or parts[1] != str(sheet.pk) or parts[3] != fingerprint(sheet.snapshot)[:16]:
            raise OCRSheetError('This printed identifier belongs to another sheet, event, round, or judge.')
        try:
            index = int(parts[2])
        except ValueError:
            raise OCRSheetError('The printed page identifier is invalid.')
        if index not in range(len(pages(sheet.snapshot))) or index in seen:
            raise OCRSheetError('The document contains duplicate or unexpected score sheet pages.')
        seen.add(index)


def extract(sheet):
    import cv2
    import numpy as np
    import pytesseract
    command = getattr(settings, 'TESSERACT_CMD', '') or shutil.which('tesseract')
    if not command and os.name == 'nt':
        default = Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'Tesseract-OCR' / 'tesseract.exe'
        command = str(default) if default.is_file() else None
    if command:
        pytesseract.pytesseract.tesseract_cmd = str(command)
    with sheet.original.open('rb') as source:
        images, _ = load_images(source.read(MAX_BYTES + 1))
    layout = pages(sheet.snapshot)
    output = {cell['key']: {'value': None, 'confidence': 0, 'needs_review': True} for page in layout for cell in page['cells']}
    deadline = time.monotonic() + 40
    found = set()
    for image in images:
        gray = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2GRAY)
        smooth = cv2.GaussianBlur(gray, (3, 3), 0)
        binary = cv2.threshold(smooth, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
        contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        aligned = None
        for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:10]:
            quad = cv2.approxPolyDP(contour, .02 * cv2.arcLength(contour, True), True)
            if len(quad) != 4 or cv2.contourArea(quad) < gray.size * .35:
                continue
            points = quad.reshape(4, 2).astype('float32')
            sums, differences = points.sum(axis=1), np.diff(points, axis=1).ravel()
            ordered = np.array([points[sums.argmin()], points[differences.argmin()], points[sums.argmax()], points[differences.argmax()]])
            target = np.array([[36, 36], [(WIDTH-18)*2,36], [(WIDTH-18)*2,(HEIGHT-18)*2], [36,(HEIGHT-18)*2]], dtype='float32')
            aligned = cv2.warpPerspective(gray, cv2.getPerspectiveTransform(ordered, target), (WIDTH*2,HEIGHT*2), borderValue=255)
            break
        if aligned is None:
            continue
        selected = None
        for rotation in range(4):
            candidate = np.rot90(aligned, rotation).copy()
            if candidate.shape != (HEIGHT * 2, WIDTH * 2):
                candidate = cv2.resize(candidate, (WIDTH*2, HEIGHT*2))
            detector = cv2.QRCodeDetector()
            text, _, _ = detector.detectAndDecode(candidate[40:310,40:310])
            if not text:
                text, _, _ = detector.detectAndDecode(candidate)
            if text.startswith('ET-OCR:'):
                parts = text.split(':')
                if len(parts) != 4 or parts[1] != str(sheet.pk) or parts[3] != fingerprint(sheet.snapshot)[:16]:
                    raise OCRSheetError('The printed identifier belongs to another score sheet or configuration.')
                try:
                    page_index = int(parts[2])
                except ValueError:
                    raise OCRSheetError('Invalid printed page identifier.')
                if page_index not in range(len(layout)) or page_index in found:
                    raise OCRSheetError('The scan has a duplicate or unexpected page.')
                selected = candidate;found.add(page_index);break
        if selected is None:
            continue
        for cell in layout[page_index]['cells']:
            if time.monotonic() >= deadline:
                break
            x,y,w,h = cell['box']
            crop = selected[int((y+3)*2):int((y+h-3)*2),int((x+4)*2):int((x+w-4)*2)]
            crop = cv2.threshold(crop,0,255,cv2.THRESH_BINARY+cv2.THRESH_OTSU)[1]
            crop = cv2.copyMakeBorder(crop,12,12,12,12,cv2.BORDER_CONSTANT,value=255)
            try:
                data = pytesseract.image_to_data(crop, config='--psm 7 -c tessedit_char_whitelist=0123456789.',
                                                 output_type=pytesseract.Output.DICT, timeout=min(3,max(.1,deadline-time.monotonic())))
            except RuntimeError:
                continue
            tokens = [(text.strip(),float(conf)) for text,conf in zip(data['text'],data['conf']) if text.strip()]
            text = ''.join(token for token,_ in tokens)
            confidence = min((conf for _,conf in tokens),default=0)
            output[cell['key']] = {'value': text if re.fullmatch(r'\d+(?:\.\d{1,2})?',text) and confidence>=60 else None,
                                   'raw': text, 'confidence': confidence, 'needs_review': True}
    if not found:
        raise OCRSheetError('No aligned EventTab template identifier was found. Use the printed template or enter scores manually.')
    columns = {col['key']: col for col in sheet.snapshot['columns']}
    for key, cell in output.items():
        col = columns[key.split('/',1)[1]]
        value = cell['value']
        if value is not None and not Decimal(col['minimum']) <= Decimal(value) <= Decimal(col['maximum']):
            cell['value'] = None
        cell['needs_review'] = cell['value'] is None or cell['confidence'] < 85
    return output
