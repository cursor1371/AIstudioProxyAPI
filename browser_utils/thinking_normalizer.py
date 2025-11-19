"""
思考模式参数归一化模块
将 reasoning_effort 参数归一化为标准化的思考指令

本模块负责将各种格式的 reasoning_effort 参数转换为统一的内部指令结构。
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
        budget_value: 预算token数量（仅当budget_enabled=True时有效，Gemini 2.5）
        thinking_level: 思考等级 "HIGH" | "LOW" (Gemini 3.0)
        original_value: 原始的reasoning_effort值（用于日志）
    """
    thinking_enabled: bool
    budget_enabled: bool
    budget_value: Optional[int]
    thinking_level: Optional[str]
    original_value: Any


def normalize_reasoning_effort(reasoning_effort: Optional[Any]) -> ThinkingDirective:
    """将 reasoning_effort 参数归一化为标准化的思考指令

    参数:
        reasoning_effort: API请求中的reasoning_effort参数
    """

    # 默认值
    directive = ThinkingDirective(
        thinking_enabled=ENABLE_THINKING_BUDGET,
        budget_enabled=ENABLE_THINKING_BUDGET,
        budget_value=DEFAULT_THINKING_BUDGET if ENABLE_THINKING_BUDGET else None,
        thinking_level=None,
        original_value=reasoning_effort
    )

    # 场景1: 用户未指定，使用默认配置
    if reasoning_effort is None:
        return directive

    # 场景2: 关闭思考模式 (reasoning_effort = 0 或 "0")
    if reasoning_effort == 0 or (isinstance(reasoning_effort, str) and reasoning_effort.strip() == "0"):
        directive.thinking_enabled = False
        directive.budget_enabled = False
        directive.budget_value = None
        directive.thinking_level = None
        return directive

    # 解析字符串类型的输入
    if isinstance(reasoning_effort, str):
        reasoning_str = reasoning_effort.strip().upper()
        
        # Gemini 3.0 / OpenAI Standard: HIGH / MEDIUM / LOW
        if reasoning_str == "LOW":
            directive.thinking_enabled = True
            directive.thinking_level = "LOW"
            directive.budget_value = 1000 # 兼容旧版的预设值
            directive.budget_enabled = True
            return directive
        
        if reasoning_str in ["HIGH", "MEDIUM"]:
            directive.thinking_enabled = True
            directive.thinking_level = "HIGH"
            directive.budget_value = 8000 if reasoning_str == "MEDIUM" else 32000
            directive.budget_enabled = True
            return directive

        # Infinite / None
        if reasoning_str in ["NONE", "-1"]:
            directive.thinking_enabled = True
            directive.budget_enabled = False
            directive.budget_value = None
            directive.thinking_level = "HIGH" # 无限制通常对应 High
            return directive

    # 处理数字类型的输入 (-1 表示无限)
    if reasoning_effort == -1:
        directive.thinking_enabled = True
        directive.budget_enabled = False
        directive.budget_value = None
        directive.thinking_level = "HIGH"
        return directive

    # 场景4: 开启思考且限制预算 (具体数字) -> Gemini 2.5
    budget_value = _parse_budget_value(reasoning_effort)

    if budget_value is not None and budget_value > 0:
        directive.thinking_enabled = True
        directive.budget_enabled = True
        directive.budget_value = budget_value
        # 简单的数字映射到等级，作为兼容 Gemini 3 的备选
        directive.thinking_level = "LOW" if budget_value < 2000 else "HIGH"
        return directive

    return directive


def _parse_budget_value(reasoning_effort: Any) -> Optional[int]:
    """解析预算值"""
    # 如果是整数，直接返回
    if isinstance(reasoning_effort, int) and reasoning_effort > 0:
        return reasoning_effort

    # 如果是字符串，尝试解析为数字
    if isinstance(reasoning_effort, str):
        try:
            value = int(reasoning_effort)
            if value > 0:
                return value
        except (ValueError, TypeError):
            pass

    return None


def format_directive_log(directive: ThinkingDirective) -> str:
    """格式化思考指令为日志字符串"""
    if not directive.thinking_enabled:
        return f"关闭思考模式 (原始值: {directive.original_value})"
    
    parts = ["开启思考"]
    if directive.thinking_level:
        parts.append(f"等级:{directive.thinking_level}")
    
    if directive.budget_enabled and directive.budget_value is not None:
        parts.append(f"预算:{directive.budget_value}")
    else:
        parts.append("无预算限制")
        
    return f"{', '.join(parts)} (原始值: {directive.original_value})"
