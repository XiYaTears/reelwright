"""运行状态、退出码与失败码常量。"""

from __future__ import annotations

from enum import Enum, IntEnum


class Status(str, Enum):
    """一次运行的四种明确状态。"""

    COMPLETED = "completed"
    NEEDS_INPUT = "needs_input"
    INCOMPLETE = "incomplete"
    FAILED = "failed"


class ExitCode(IntEnum):
    """进程退出码（与 01 §4.3 一致）。"""

    COMPLETED = 0
    INTERNAL_ERROR = 1
    USAGE_ERROR = 2
    NEEDS_INPUT = 3
    INCOMPLETE = 4
    FAILED = 5


EXIT_CODE_BY_STATUS: dict[Status, ExitCode] = {
    Status.COMPLETED: ExitCode.COMPLETED,
    Status.NEEDS_INPUT: ExitCode.NEEDS_INPUT,
    Status.INCOMPLETE: ExitCode.INCOMPLETE,
    Status.FAILED: ExitCode.FAILED,
}


class FailureCode(str, Enum):
    """失败/未完成原因码（写入 output.failure.code 与 trace）。"""

    NO_SCRIPT_SUBMITTED = "no_script_submitted"
    TOOL_FAILURE_UNRECOVERED = "tool_failure_unrecovered"
    INVALID_ACTION_COMBINATION = "invalid_action_combination"
    CANNOT_SATISFY = "cannot_satisfy"
    CHECKS_FAILED_AFTER_FINISH = "checks_failed_after_finish"
    MODEL_TERMINATED_WITHOUT_FINISH = "model_terminated_without_finish"
    STEP_LIMIT = "step_limit"
    TOOL_CALL_LIMIT = "tool_call_limit"
    LLM_ERROR = "llm_error"
    ORCHESTRATION_ERROR = "orchestration_error"
