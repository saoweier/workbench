"""Local immutable media; generated through the application's configured API."""
import base64
import hashlib
import io
import json
import re
from pathlib import Path

from PIL import Image, ImageOps

from ..core.config import get_settings
from ..core.errors import NotFound, StateConflict, ValidationFailed


class ContentMedia:
    def __init__(self,runtime=None):
        self.root=get_settings().storage_root/'content-media'
        self.runtime=runtime

    def store(self,raw,*,content_id=None,description,origin,request_key=None):
        if len(raw)>20_000_000:raise ValidationFailed('图片超过20MB')
        try:
            with Image.open(io.BytesIO(raw)) as original:
                if original.width*original.height>25_000_000 or min(original.size)<160:
                    raise ValidationFailed('配图尺寸应至少160像素，且不超过2500万像素')
                im=ImageOps.exif_transpose(original).convert('RGB')
                im.thumbnail((1800,1800))
                buffer=io.BytesIO();im.save(buffer,format='PNG');data=buffer.getvalue()
        except ValidationFailed:raise
        except Exception as exc:raise ValidationFailed('无法读取这张图片，请上传PNG或JPEG') from exc
        sha=hashlib.sha256(data).hexdigest()
        mid='media-'+hashlib.sha256(((content_id or '')+sha).encode()).hexdigest()[:32]
        folder=self.root/mid;folder.mkdir(parents=True,exist_ok=True)
        metadata={'id':mid,'sha256':sha,'content_id':content_id,'description':description[:700],
            'origin':origin,'disclosure':'AI生成示意图，非实拍' if origin=='generated' else '用户提供素材，请确认使用权',
            'width':im.width,'height':im.height,'request_key':request_key}
        if not (folder/'metadata.json').exists():
            (folder/'image.png').write_bytes(data)
            (folder/'metadata.json').write_text(json.dumps(metadata,ensure_ascii=False),encoding='utf-8')
        return self.get(mid)

    def get(self,mid):
        if not isinstance(mid,str) or not re.fullmatch(r'media-[0-9a-f]{32}',mid):raise NotFound('图片不存在')
        folder=self.root/mid
        if not (folder/'metadata.json').exists():raise NotFound('图片不存在')
        metadata=json.loads((folder/'metadata.json').read_text(encoding='utf-8'))
        if hashlib.sha256((folder/'image.png').read_bytes()).hexdigest()!=metadata['sha256']:
            raise ValidationFailed('图片已变化，无法复用旧素材')
        return metadata

    def uri(self,mid):
        self.get(mid)
        return 'data:image/png;base64,'+base64.b64encode((self.root/mid/'image.png').read_bytes()).decode()

    def path(self,mid):
        self.get(mid);return self.root/mid/'image.png'

    def create_for_plan(self,plan,*,content_id,context_id,provided_ids=(),image_policy='auto'):
        provided=[self.get(mid) for mid in provided_ids]
        for value in provided:
            if value['content_id'] not in {None,content_id}:
                raise ValidationFailed('配图不属于这条内容')
        pages=[p for p in plan.pages if p.visual_type!='diagram']
        if provided and not pages:pages=plan.pages[:len(provided)]
        if image_policy=='diagram':return []
        if pages and not provided and (not self.runtime or not self.runtime.image_provider()):
            raise ValidationFailed('内容规划需要实物配图。请在API设置配置图片模型，或在预览页上传配图后重做；文字模型不会自动生成图片。')
        result=[]
        for page in pages[:6]:
            if provided:
                value=provided[(page.index-1)%len(provided)]
            else:
                prompt=page.visual_brief+'\n为中文图文产品制作主体明确的高质量配图。不要生成文字、统计数据、营养表、品牌标识或水印。'
                call,response=self.runtime.generate_image(prompt=prompt,content_id=content_id,request_key=f'{context_id}:page:{page.index}',prompt_version='skill.generation.image.v1')
                if not response.ok:raise StateConflict(f'图片生成未完成（{response.error_code}），没有自动重发')
                try:raw=base64.b64decode(response.parsed['b64_json'],validate=True)
                except Exception as exc:raise ValidationFailed('图片接口没有返回有效图片') from exc
                value=self.store(raw,content_id=content_id,description=page.visual_brief,origin='generated',request_key=call.request_key)
            result.append({**value,'plan_page':page.index,'heading':page.heading})
        return result
