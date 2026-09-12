"""权限控制模块公开接口。"""

from .approval import ApprovalHandler, ConfirmAction, InteractiveApprovalHandler
from .enforcer import PermissionEnforcer, PermissionOutcome
from .policy import (
    DANGEROUS_SHELL_PATTERNS,
    Decision,
    PermissionPolicy,
    PermissionRequest,
    PermissionRule,
    args_to_text,
    build_default_policy,
    canonical_permission_args,
    parse_rule,
)

__all__ = [
    "ApprovalHandler",
    "ConfirmAction",
    "InteractiveApprovalHandler",
    "PermissionEnforcer",
    "PermissionOutcome",
    "DANGEROUS_SHELL_PATTERNS",
    "Decision",
    "PermissionPolicy",
    "PermissionRequest",
    "PermissionRule",
    "args_to_text",
    "build_default_policy",
    "canonical_permission_args",
    "parse_rule",
]
