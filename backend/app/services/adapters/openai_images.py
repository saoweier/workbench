"""OpenAI compatible Images adapter; accepts inline base64, never model URLs."""
import time
from .base import BaseAdapter, AdapterResult
from ..provider_contract import AdapterType, RunMode


class OpenAIImagesAdapter(BaseAdapter):
    adapter_type=AdapterType.OPENAI_IMAGES

    def complete(self,prompt,*,system=None,max_tokens=None,json_schema=None,request_key=None):
        return self.generate(prompt,request_key=request_key)

    def generate(self,prompt,*,request_key=None):
        if not self.api_key:return self._missing_key()
        if not self.cfg.model_id:return self._missing_key('model_id')
        if self.transport is None:return AdapterResult(ok=False,error_code='UNKNOWN',error_message='图片传输未配置')
        body={'model':self.cfg.model_id,'prompt':prompt,'n':1,'size':'1024x1024'}
        if not self.cfg.model_id.startswith(('gpt-image-','chatgpt-image-')):body['response_format']='b64_json'
        url=self.cfg.base_url.rstrip('/')
        if not url.endswith('/images/generations'):url+='/images/generations'
        started=time.monotonic()
        try:
            response=self.transport.request('POST',url,headers={'Authorization':'Bearer '+self.api_key,'Content-Type':'application/json'},json_body=body,timeout=self.cfg.timeout_seconds)
        except Exception:
            return AdapterResult(ok=False,error_code='NETWORK_TIMEOUT',error_message='图片请求未取得确认结果；停止自动重发',run_mode=RunMode.REAL)
        if response.status_code!=200:
            code='AUTH' if response.status_code in {401,403} else 'RATE_LIMIT' if response.status_code==429 else 'SERVER_ERROR'
            return AdapterResult(ok=False,error_code=code,error_message='图片接口返回HTTP '+str(response.status_code),http_status=response.status_code,run_mode=RunMode.REAL)
        data=response.json_body or {};images=data.get('data') or []
        if len(images)!=1 or not isinstance(images[0],dict) or not isinstance(images[0].get('b64_json'),str):
            return AdapterResult(ok=False,error_code='BAD_JSON',error_message='图片接口须支持b64_json响应；不自动读取远程图片URL',run_mode=RunMode.REAL)
        if len(images[0]['b64_json'])>28_000_000:
            return AdapterResult(ok=False,error_code='BAD_JSON',error_message='图片响应超过20MB上限',run_mode=RunMode.REAL)
        usage=data.get('usage') or {}
        return AdapterResult(ok=True,parsed={'b64_json':images[0]['b64_json']},usage_raw=usage or None,
            input_tokens=usage.get('input_tokens'),output_tokens=usage.get('output_tokens'),
            remote_request_id=response.headers.get('x-request-id'),http_status=200,
            latency_ms=int((time.monotonic()-started)*1000),run_mode=RunMode.REAL)

    def test_connection(self):
        # Do not accidentally buy an image when testing connectivity.
        return AdapterResult(ok=False,error_code='IMAGE_TEST_REQUIRES_GENERATION',error_message='请在创作流程中显式生成一张图片以测试；保存配置不产生费用',run_mode=RunMode.LOCAL_SEED)
