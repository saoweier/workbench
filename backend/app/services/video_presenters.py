"""Local, immutable image assets for video presenters."""
import hashlib
from io import BytesIO
import json
from pathlib import Path
import re
from uuid import uuid4

from PIL import Image, ImageOps, UnidentifiedImageError

from ..core.config import PROJECT_ROOT
from ..core.errors import NotFound, StateConflict, ValidationFailed

MAX_BYTES = 10 * 1024 * 1024


class PresenterStore:
    def __init__(self, settings, kind='video-presenters', include_examples=True):
        self.root = settings.artifact_dir / kind
        self.url_prefix = f'/api/v1/{kind}'
        self.include_examples = include_examples

    def save(self, data, name):
        if not data or len(data) > MAX_BYTES:
            raise ValidationFailed('请选择不超过 10 MB 的 PNG、JPEG 或 WebP 图片。')
        try:
            with Image.open(BytesIO(data)) as source:
                if source.format not in {'PNG', 'JPEG', 'WEBP'} or getattr(source, 'n_frames', 1) != 1:
                    raise ValidationFailed('仅支持静态 PNG、JPEG 和 WebP 图片。')
                if min(source.size) < 32 or max(source.size) > 4096 or source.width * source.height > 16000000:
                    raise ValidationFailed('图片边长须为 32–4096 像素，总像素不超过 1600 万。')
                image = ImageOps.exif_transpose(source).convert('RGBA')
                bounds = image.getchannel('A').getbbox()
                if not bounds:
                    raise ValidationFailed('图片完全透明，请选择可见的角色图片。')
                image = image.crop(bounds)
                output = BytesIO()
                image.save(output, format='PNG')
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
            raise ValidationFailed('图片无法读取，请重新导出为 PNG、JPEG 或 WebP。') from exc
        content = output.getvalue()
        asset_id = hashlib.sha256(content).hexdigest()
        self.root.mkdir(parents=True, exist_ok=True)
        info = {'id': asset_id, 'name': Path(name.replace('\\', '/')).name[:120] or '自定义讲解员',
                'width': image.width, 'height': image.height}
        # Atomic replacement keeps concurrent uploads from leaving partial assets.
        for suffix, body in [('.png', content), ('.json', json.dumps(info, ensure_ascii=False).encode('utf-8'))]:
            destination = self.root / (asset_id + suffix)
            if not destination.exists():
                temporary = self.root / f'{asset_id}.{uuid4().hex}.tmp'
                temporary.write_bytes(body)
                temporary.replace(destination)
        return self.get(asset_id)

    def path(self, asset_id):
        if not re.fullmatch(r'[a-f0-9]{64}', asset_id or ''):
            raise NotFound('讲解员图片不存在。')
        path = self.root / (asset_id + '.png')
        if not path.is_file():
            raise NotFound('讲解员图片不存在，请重新上传。')
        if hashlib.sha256(path.read_bytes()).hexdigest() != asset_id:
            raise StateConflict('讲解员图片已发生变化，请重新上传后生成。')
        return path

    def get(self, asset_id):
        self.path(asset_id)
        metadata = self.root / (asset_id + '.json')
        if not metadata.is_file():
            raise NotFound('讲解员图片信息缺失，请重新上传。')
        info = json.loads(metadata.read_text(encoding='utf-8'))
        return {**info, 'image_url': f'{self.url_prefix}/{asset_id}/image'}

    def list(self):
        # Project images are explicit local examples; never accept client filesystem paths.
        examples = PROJECT_ROOT / 'images'
        if self.include_examples and examples.is_dir():
            for path in sorted(examples.iterdir()):
                if path.is_file() and not path.is_symlink() and path.suffix.lower() in {'.png', '.jpg', '.jpeg', '.webp'}:
                    if path.stat().st_size <= MAX_BYTES:
                        try:
                            self.save(path.read_bytes(), path.name)
                        except ValidationFailed:
                            continue
        items = []
        if self.root.is_dir():
            for path in sorted(self.root.glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True):
                try:
                    items.append(self.get(path.stem))
                except (NotFound, StateConflict, ValueError):
                    continue
        return {'items': items}
