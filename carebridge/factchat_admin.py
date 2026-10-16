"""Explicit administrator metadata lookup; never sends document data.

python -m carebridge.factchat_admin models   # exactly one GET, no retry
python -m carebridge.factchat_admin credits  # exactly one GET, no retry
"""
import argparse
import json
import os
import sys
import requests
from dotenv import load_dotenv
from .client import ROOT
from .factchat import BASE


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('resource', choices=['models', 'credits'])
    args = parser.parse_args()
    load_dotenv(ROOT / '.env')
    key = (os.environ.get('FACTCHAT_API_KEY') or '').strip()
    if not key:
        print('FACTCHAT_API_KEY를 비공개 환경변수에 설정해 주세요. 요청하지 않았습니다.', file=sys.stderr)
        return 1
    try:
        with requests.Session() as session:
            response = session.get(BASE+'/'+args.resource+'/', headers={'Authorization': 'Bearer '+key},
                                   params={'type': 'llm'} if args.resource == 'models' else None,
                                   timeout=(5, 15), allow_redirects=False)
        if response.status_code != 200:
            print(f'Gateway 조회 실패: HTTP {response.status_code}. 응답 본문은 표시하지 않습니다.', file=sys.stderr)
            return 1
        raw = response.json()
        if args.resource == 'models':
            # Official model cards supply dynamic pricing. Never invent a cheap default.
            output = [{'id': item['id'], 'type': item.get('type'), 'pricing': item.get('pricing')}
                      for item in raw['data'] if item.get('type') == 'llm']
        else:
            output = {'total': raw.get('total')}
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0
    except (requests.RequestException, ValueError, KeyError, TypeError, AttributeError):
        print('Gateway 조회 실패. 인증·네트워크·응답 형식을 확인해 주세요. 자동 재요청하지 않습니다.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
