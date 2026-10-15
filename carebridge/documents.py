"""Validate uploads and convert raster photos into a single-page PDF for the Agent."""
import io
import warnings
from PIL import Image, ImageOps, UnidentifiedImageError
from .client import AnalysisError, validate_pdf

MAX_BYTES=20*1024*1024
MAX_PIXELS=30_000_000

def prepare_document(data):
    if not data or len(data)>MAX_BYTES:
        raise AnalysisError('20MB 이하의 PDF, PNG 또는 JPG 파일을 선택해 주세요.')
    if data.startswith(b'%PDF-'):
        validate_pdf(data)
        return data
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error',Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as source:
                if source.format not in ('PNG','JPEG') or source.width*source.height>MAX_PIXELS:
                    raise AnalysisError('PNG·JPG 사진만 지원하며, 사진은 3천만 화소 이하여야 합니다.')
                if getattr(source,'n_frames',1)!=1:
                    raise AnalysisError('움직이는 PNG는 지원하지 않습니다. 정지 사진을 선택해 주세요.')
                source.load()
                oriented=ImageOps.exif_transpose(source)
                rgba=oriented.convert('RGBA')
                canvas=Image.new('RGB',rgba.size,'white')
                canvas.paste(rgba,mask=rgba.getchannel('A'))
                out=io.BytesIO();canvas.save(out,format='PDF',resolution=150.0)
                pdf=out.getvalue()
                if len(pdf)>MAX_BYTES:
                    raise AnalysisError('변환된 사진이 20MB를 넘습니다. 사진 크기를 줄여 주세요.')
                return pdf
    except AnalysisError:
        raise
    except (UnidentifiedImageError,OSError,ValueError,Image.DecompressionBombError,Image.DecompressionBombWarning):
        raise AnalysisError('사진을 읽을 수 없습니다. 정상 PNG·JPG 파일인지 확인해 주세요.') from None

def original_type(data):
    if data.startswith(b'%PDF-'):return 'original.pdf','application/pdf'
    if data.startswith(b'\x89PNG'):return 'original.png','image/png'
    return 'original.jpg','image/jpeg'
