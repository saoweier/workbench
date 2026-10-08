"""Add one maintenance link while preserving the concurrently edited UI."""
from pathlib import Path
p=Path('frontend/src/views/SkillWorkflow.html');text=p.read_text(encoding='utf-8')
old='<a class="btn" href="/views/ApiSettings.html">配置模型与图片能力</a>'
new='<a class="btn" href="/views/TemplateLibrary.html">样式与模板 Skill</a>'+old
if 'href="/views/TemplateLibrary.html"' not in text:
    assert text.count(old)==1
    p.write_text(text.replace(old,new),encoding='utf-8')
