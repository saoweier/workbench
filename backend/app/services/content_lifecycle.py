"""State transitions must never restore content that the user cleared."""
from sqlalchemy import update
from ..models.entities import ContentItem


def update_content_state(session, content, state: str) -> bool:
    # Check in SQL, including when this session holds a pre-clear cached object.
    result = session.execute(
        update(ContentItem).where(ContentItem.id == content.id,
                                  ContentItem.state != 'discarded').values(state=state),
        execution_options={'synchronize_session': 'fetch'},
    )
    return bool(result.rowcount)
