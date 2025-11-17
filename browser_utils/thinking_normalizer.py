# browser_utils/thinking_normalizer.py

"""
思考模式参数归一化模块
将 reasoning_effort 或 extra_body.google.thinking_config 参数归一化为标准化的思考指令
"""

from typing import Optional, Any, Dict
from dataclasses import dataclass
from config import ENABLE_THINKING_BUDGET, DEFAULT_THINKING_BUDGET


@dataclass
class ThinkingDirective:
    """标准化的思考指令

    属性:
        thinking_enabled: 是否启用思考模式（总开关）
        budget_enabled: 是否限制思考预算
        budget_value: 预算token数量（仅当budget_enabled=True时有效）
        original_value: 原始的reasoning_effort或thinking_config值（用于日志）
    """
    thinking_enabled: bool
    budget_enabled: bool
    budget_value: Optional[int]
    original_value: Any


def normalize_thinking_directive(request_params: Dict[str, Any]) -> ThinkingDirective:
    """
    将请求参数中的思考模式配置归一化为标准指令。
    优先级: extra_body.google.thinking_config > reasoning_effort > 默认配置
    """
    
    # 优先级1: 检查标准的 extra_body.google.thinking_config
    extra_body = request_params.get("extra_body", {})
    if isinstance(extra_body, dict):
        google_config = extra_body.get("google", {})
        if isinstance(google_config, dict):
            thinking_config = google_config.get("thinking_config", {})
            if isinstance(thinking_config, dict) and thinking_config.get("include_thoughts") is True:
                budget = thinking_config.get("thinking_budget")
                if budget is not None:
                    try:
                        budget_val = int(budget)
                        if budget_val >= 0: # 预算为0也视为开启预算限制
                            return ThinkingDirective(True, True, budget_val, thinking_config)
                    except (ValueError, TypeError):
                        pass # 解析失败则忽略
                # include_thoughts=True 但没有有效预算，视为不限制预算
                return ThinkingDirective(True, False, None, thinking_config)

    # 优先级2: 回退检查自定义的 reasoning_effort 参数
    reasoning_effort = request_params.get('reasoning_effort')
    if reasoning_effort is not None:
        # 场景2.1: 关闭思考模式 (reasoning_effort = 0 或 "0")
        if reasoning_effort == 0 or (isinstance(reasoning_effort, str) and reasoning_effort.strip() == "0"):
            return ThinkingDirective(False, False, None, reasoning_effort)

        # 场景2.2: 开启思考但不限制预算 (reasoning_effort = "none" / "-1" / -1)
        if isinstance(reasoning_effort, str):
            reasoning_str = reasoning_effort.strip().lower()
            if reasoning_str in ["none", "-1"]:
                return ThinkingDirective(True, False, None, reasoning_effort)
        elif reasoning_effort == -1:
            return ThinkingDirective(True, False, None, reasoning_effort)

        # 场景2.3: 开启思考且限制预算 (具体数字或预设值)
        budget_value = _parse_budget_value(reasoning_effort)
        if budget_value is not None:
             # budget_value 为 0 也视为启用预算限制
            return ThinkingDirective(True, True, budget_value, reasoning_effort)
    
    # 优先级3: 用户未指定任何相关参数，使用.env中的默认配置
    return ThinkingDirective(
        thinking_enabled=ENABLE_THINKING_BUDGET,
        budget_enabled=ENABLE_THINKING_BUDGET,
        budget_value=DEFAULT_THINKING_BUDGET if ENABLE_THINKING_BUDGET else None,
        original_value=None
    )


def _parse_budget_value(reasoning_effort: Any) -> Optional[int]:
    """解析预算值"""
    # 如果是整数
    if isinstance(reasoning_effort, int):
        return reasoning_effort # 允许0和-1

    # 如果是字符串
    if isinstance(reasoning_effort, str):
        effort_str = reasoning_effort.strip().lower()
        effort_map = {"low": 1000, "medium": 8000, "high": 24000}
        if effort_str in effort_map:
            return effort_map[effort_str]
        try:
            return int(effort_str)
        except (ValueError, TypeError):
            pass

    return None


def format_directive_log(directive: ThinkingDirective) -> str:
    """格式化思考指令为日志字符串"""
    original_value_str = f"(原始值: {directive.original_value})" if directive.original_value is not None else "(使用默认配置)"
    
    if not directive.thinking_enabled:
        return f"关闭思考模式 {original_value_str}"
    
    if directive.budget_enabled and directive.budget_value is not None:
        return f"开启思考并限制预算: {directive.budget_value} tokens {original_value_str}"
    
    return f"开启思考，不限制预算 {original_value_str}"
