"""An owned task may not run a vanished or another owner's draft agent."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from xagent.web.models.agent import Agent, AgentStatus
from xagent.web.models.database import Base
from xagent.web.models.task import Task
from xagent.web.models.user import User
from xagent.web.services.agent_service_manager import _load_agent_for_task_runtime


def test_missing_and_foreign_draft_agents_are_not_executable_as_task_owner(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'agent-visibility.db'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    try:
        with sessions() as db:
            owner = User(username="channel-owner", password_hash="unused")
            stranger = User(username="other-owner", password_hash="unused")
            db.add_all([owner, stranger])
            db.flush()
            foreign_draft = Agent(
                user_id=stranger.id, name="Private draft", status=AgentStatus.DRAFT
            )
            db.add(foreign_draft)
            db.flush()
            task = Task(user_id=owner.id, title="Local task", agent_id=foreign_draft.id)
            db.add(task)
            db.commit()
            assert _load_agent_for_task_runtime(db, task) is None
            task.agent_id = 999999
            db.flush()
            assert _load_agent_for_task_runtime(db, task) is None
    finally:
        engine.dispose()
