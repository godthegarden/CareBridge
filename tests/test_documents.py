import io
import pytest
from PIL import Image
from pypdf import PdfReader
from carebridge.documents import prepare_document, original_type
from carebridge.client import AnalysisError

@pytest.mark.parametrize('fmt,mode',[('PNG','RGBA'),('JPEG','RGB')])
def test_photo_converts_to_pdf(fmt,mode):
    buf=io.BytesIO();Image.new(mode,(120,80),'white').save(buf,format=fmt)
    source=buf.getvalue();pdf=prepare_document(source)
    assert pdf.startswith(b'%PDF-') and len(PdfReader(io.BytesIO(pdf)).pages)==1
    assert original_type(source)[1]==('image/png' if fmt=='PNG' else 'image/jpeg')

def test_reject_unsupported_image():
    buf=io.BytesIO();Image.new('RGB',(10,10)).save(buf,format='GIF')
    with pytest.raises(AnalysisError):prepare_document(buf.getvalue())

def test_corrupt_photo():
    with pytest.raises(AnalysisError):prepare_document(b'\x89PNG\r\ninvalid')

def test_exif_orientation():
    buf=io.BytesIO();im=Image.new('RGB',(120,80));exif=Image.Exif();exif[274]=6;im.save(buf,format='JPEG',exif=exif)
    page=PdfReader(io.BytesIO(prepare_document(buf.getvalue()))).pages[0]
    assert float(page.mediabox.height)>float(page.mediabox.width)
