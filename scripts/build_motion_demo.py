"""Re-render an existing completed narration as a dynamic demo; no provider calls."""
from pathlib import Path
import sys,json
from sqlalchemy import select

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
from app.core.config import get_settings
from app.models.entities import VideoTask
from app.services.video_service import VideoService
from app.services.video_presenters import PresenterStore
from app.services.video_storyboard import plan
from app.services.video_render import render

def main():
    service=VideoService(get_settings())
    try:
        with service.sf() as session:
            tasks=list(session.scalars(select(VideoTask).where(VideoTask.state=='succeeded').order_by(VideoTask.created_at)))
            task=next(t for t in tasks if all((service.root/t.id/f'page-{i+1:02}.wav').is_file() for i in range(len(t.payload_json['pages']))))
            payload=dict(task.payload_json);task_id=task.id
        payload.update(animation_style='presentation',storyboard=plan(payload['pages']),presenter='custom')
        asset_store=PresenterStore(service.settings)
        asset=asset_store.save((ROOT/'images/img_anon_1.png').read_bytes(),'img_anon_1.png')
        payload['presenter_image_path']=str(asset_store.path(asset['id']))
        images=[service.image_path(page) for page in payload['pages']]
        audio=[service.root/task_id/f'page-{i+1:02}.wav' for i in range(len(images))]
        output=ROOT/'docs/test-artifacts/motion-demo';output.mkdir(parents=True,exist_ok=True)
        result=render(payload,images,audio,output,lambda p:print(f'Rendering {p}%',flush=True) if p%10==0 else None)
        destination=ROOT/'frontend/src/assets/demo-motion-narration.mp4'
        import shutil
        shutil.copy2(output/'video.mp4',destination)
        (output/'manifest.json').write_text(json.dumps({'source_task':task_id,'source_payload_hash':task.payload_hash,'director':payload['storyboard']['director'],'reused_audio':True,'result':result},ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'path':str(destination),'result':result},ensure_ascii=False))
    finally:service.engine.dispose()

if __name__=='__main__':main()
