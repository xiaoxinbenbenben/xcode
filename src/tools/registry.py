"""集中导出主代理会挂载的工具清单。"""

from src.tools.bash_tool import BASH_TOOLS
from src.tools.compaction_tool import COMPACTION_TOOLS
from src.tools.edit_write import FILE_EDIT_TOOLS
from src.tools.read_only import READ_ONLY_TOOLS
from src.tools.skill_tool import SKILL_TOOLS
from src.tools.task_tools import TASK_TOOLS
from src.tools.team_tools import TEAM_TOOLS
from src.tools.todo_write import TODO_TOOLS

AGENT_TOOLS = [
    *READ_ONLY_TOOLS,
    *FILE_EDIT_TOOLS,
    *TODO_TOOLS,
    *BASH_TOOLS,
    *COMPACTION_TOOLS,
    *TASK_TOOLS,
    *SKILL_TOOLS,
    *TEAM_TOOLS,
]

__all__ = [
    "READ_ONLY_TOOLS",
    "FILE_EDIT_TOOLS",
    "TODO_TOOLS",
    "BASH_TOOLS",
    "COMPACTION_TOOLS",
    "TASK_TOOLS",
    "SKILL_TOOLS",
    "TEAM_TOOLS",
    "AGENT_TOOLS",
]
