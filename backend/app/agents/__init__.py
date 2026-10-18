"""agent 注册入口。import 即注册 run handler。"""
from app.agents import (  # noqa: F401
    chat_agent,
    chat_tools,
    evaluation_pipeline,
    interview_pipeline,
    jd_parser,
    project_pipeline,
    resume_pipeline,
)

__all__ = [
    "chat_agent",
    "chat_tools",
    "evaluation_pipeline",
    "interview_pipeline",
    "jd_parser",
    "project_pipeline",
    "resume_pipeline",
]
