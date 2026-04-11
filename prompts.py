from __future__ import annotations


"""
Backward-compatible shim.

Prefer importing from:
- prompts.registry
- prompts.path_structure
- prompts.node_teaching
- prompts.chat_intake
"""

from prompts.path_structure import SYSTEM as SYSTEM_PATH_STRUCTURE
from prompts.path_structure import user_prompt as user_prompt_structure
from prompts.node_teaching import SYSTEM as SYSTEM_NODE_TEACHING
from prompts.node_teaching import user_prompt as user_prompt_node_teaching

