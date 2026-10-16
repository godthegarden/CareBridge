from carebridge.ui import badge,lines,summary_html,table_html


def test_document_values_are_escaped_in_results():
    text='<script>alert("private")</script>'
    html=table_html(['약품명'],[[lines(text)]],'원문 목록')
    assert '<script>' not in html and '&lt;script&gt;' in html
    assert '<table aria-label="원문 목록">' in html
    assert '<th scope="col">약품명</th>' in html
    assert 'data-label="약품명"' in html
    assert '<script>' not in summary_html([(text,text)])
    assert '<script>' not in badge(text,True)


def test_health_statuses_use_distinct_colors_and_visible_text():
    assert badge('문서 범위 내',tone='normal')=='<span class="status-badge">문서 범위 내</span>'
    assert 'status-badge attention' in badge('원문 판정 · 과체중',tone='caution')
    assert 'status-badge danger' in badge('문서 범위 초과',tone='abnormal')
    assert 'status-badge muted' in badge('확인 필요',tone='unknown')
    assert 'status-badge attention' in badge('복약 확인 필요',attention=True)
