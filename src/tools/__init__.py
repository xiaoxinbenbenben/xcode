"""本地工具实现。"""

from src.tools.bash_tool import BASH_TOOLS
from src.tools.edit_write import FILE_EDIT_TOOLS
from src.tools.read_only import READ_ONLY_TOOLS
from src.tools.todo_write import TODO_TOOLS

AGENT_TOOLS = [*READ_ONLY_TOOLS, *FILE_EDIT_TOOLS, *TODO_TOOLS, *BASH_TOOLS]

__all__ = ["READ_ONLY_TOOLS", "FILE_EDIT_TOOLS", "TODO_TOOLS", "BASH_TOOLS", "AGENT_TOOLS"]
