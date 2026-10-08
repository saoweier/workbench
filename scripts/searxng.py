"""Manage the optional, loopback-only search container for this checkout."""
from __future__ import annotations
import argparse
import json
import secrets
import subprocess
import sys
import time
from pathlib import Path
import httpx

ROOT=Path(__file__).resolve().parents[1]
COMPOSE=ROOT/'deploy/searxng/compose.yml'
CONFIG=ROOT/'storage/searxng/config/settings.yml'


def compose(*args):
    return subprocess.run(['docker','compose','-f',str(COMPOSE),*args],cwd=ROOT,
                          creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0,
                          check=True)


def main():
    parser=argparse.ArgumentParser(description='本地 SearXNG 搜索服务')
    parser.add_argument('action',choices=['start','stop','status','logs','probe'])
    parser.add_argument('--query',default='SearXNG')
    args=parser.parse_args()
    if args.action=='start':
        if not CONFIG.exists():
            CONFIG.parent.mkdir(parents=True,exist_ok=True)
            template=(ROOT/'deploy/searxng/settings.yml.example').read_text(encoding='utf-8')
            CONFIG.write_text(template.replace('__GENERATED_SECRET__',secrets.token_hex(32)),encoding='utf-8')
        compose('up','-d')
        for _ in range(30):
            try:
                response=httpx.get('http://127.0.0.1:8088/',timeout=2)
                if response.status_code==200:
                    print('SearXNG 已启动：http://127.0.0.1:8088；请在 API 设置选择 SearXNG 并测试原题。')
                    return
            except httpx.HTTPError:pass
            time.sleep(1)
        raise RuntimeError('搜索服务未就绪，请运行 logs 查看本项目容器日志。')
    elif args.action=='stop':compose('stop')
    elif args.action=='status':compose('ps')
    elif args.action=='logs':compose('logs','--tail','60')
    else:
        response=httpx.get('http://127.0.0.1:8088/search',params={'q':args.query,'format':'json'},timeout=30)
        response.raise_for_status()
        result=response.json()
        print(json.dumps({'query':args.query,'results':result.get('results',[])[:8],
                          'unresponsive_engines':result.get('unresponsive_engines',[])},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
