"""Escaped, responsive presentation shared by both analysis screens."""
from html import escape


def summary_html(items):
    return '<div class="result-summary" style="--summary-columns:'+str(len(items))+'">'+''.join(
        '<div class="summary-item"><span>'+escape(label)+'</span><strong>'+escape(str(value))+'</strong></div>'
        for label,value in items)+'</div>'


def badge(text, attention=False, tone=None):
    suffix={'normal':'','caution':' attention','abnormal':' danger','unknown':' muted'}.get(tone,' attention' if attention else '')
    return '<span class="status-badge'+suffix+'">'+escape(text)+'</span>'


def lines(*values):
    return '<br>'.join(escape(str(value)) for value in values)


def table_html(headers, rows, label):
    # Cells are constructed with lines()/badge(); all document strings are escaped there.
    head=''.join('<th scope="col">'+escape(h)+'</th>' for h in headers)
    body=''.join('<tr>'+''.join('<td data-label="'+escape(h,quote=True)+'">'+cell+'</td>' for h,cell in zip(headers,row))+'</tr>' for row in rows)
    return '<div class="result-table"><table aria-label="'+escape(label,quote=True)+'"><thead><tr>'+head+'</tr></thead><tbody>'+body+'</tbody></table></div>'
