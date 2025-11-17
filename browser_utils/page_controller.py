# browser_utils/page_controller.py

import asyncio
import re
from typing import Callable, List, Dict, Any, Optional

from playwright.async_api import Page as AsyncPage, expect as expect_async, TimeoutError

from config import (
    TEMPERATURE_INPUT_SELECTOR, MAX_OUTPUT_TOKENS_SELECTOR, STOP_SEQUENCE_INPUT_SELECTOR,
    MAT_CHIP_REMOVE_BUTTON_SELECTOR, TOP_P_INPUT_SELECTOR, SUBMIT_BUTTON_SELECTOR,
    PROMPT_TEXTAREA_SELECTOR, RESPONSE_CONTAINER_SELECTOR, RESPONSE_TEXT_SELECTOR,
    EDIT_MESSAGE_BUTTON_SELECTOR, USE_URL_CONTEXT_SELECTOR, UPLOAD_BUTTON_SELECTOR,
    ENABLE_THINKING_MODE_TOGGLE_SELECTOR, SET_THINKING_BUDGET_TOGGLE_SELECTOR, THINKING_BUDGET_INPUT_SELECTOR,
    GROUNDING_WITH_GOOGLE_SEARCH_TOGGLE_SELECTOR, AI_STUDIO_URL_PATTERN
)
from config import (
    CLICK_TIMEOUT_MS, WAIT_FOR_ELEMENT_TIMEOUT_MS, CLEAR_CHAT_VERIFY_TIMEOUT_MS,
    DEFAULT_TEMPERATURE, DEFAULT_MAX_OUTPUT_TOKENS, DEFAULT_STOP_SEQUENCES, DEFAULT_TOP_P,
    ENABLE_URL_CONTEXT, ENABLE_THINKING_BUDGET, DEFAULT_THINKING_BUDGET, ENABLE_GOOGLE_SEARCH
)
from models import ClientDisconnectedError
from .operations import save_error_snapshot, _wait_for_response_completion, _get_final_response_content
from .thinking_normalizer import normalize_reasoning_effort, format_directive_log

class PageController:
    """封装了与AI Studio页面交互的所有操作。"""

    def __init__(self, page: AsyncPage, logger, req_id: str):
        self.page = page
        self.logger = logger
        self.req_id = req_id

    async def _check_disconnect(self, check_client_disconnected: Callable, stage: str):
        """检查客户端是否断开连接。"""
        if check_client_disconnected(stage):
            raise ClientDisconnectedError(f"[{self.req_id}] Client disconnected at stage: {stage}")

    async def adjust_parameters(self, request_params: Dict[str, Any], page_params_cache: Dict[str, Any], params_cache_lock: asyncio.Lock, model_id_to_use: str, parsed_model_list: List[Dict[str, Any]], check_client_disconnected: Callable):
        """调整所有请求参数。"""
        self.logger.info(f"[{self.req_id}] 开始调整所有请求参数...")
        await self._check_disconnect(check_client_disconnected, "Start Parameter Adjustment")

        # 调整温度
        temp_to_set = request_params.get('temperature', DEFAULT_TEMPERATURE)
        await self._adjust_temperature(temp_to_set, page_params_cache, params_cache_lock, check_client_disconnected)
        await self._check_disconnect(check_client_disconnected, "After Temperature Adjustment")

        # 调整最大Token
        max_tokens_to_set = request_params.get('max_output_tokens', DEFAULT_MAX_OUTPUT_TOKENS)
        await self._adjust_max_tokens(max_tokens_to_set, page_params_cache, params_cache_lock, model_id_to_use, parsed_model_list, check_client_disconnected)
        await self._check_disconnect(check_client_disconnected, "After Max Tokens Adjustment")

        # 调整停止序列
        stop_to_set = request_params.get('stop', DEFAULT_STOP_SEQUENCES)
        await self._adjust_stop_sequences(stop_to_set, page_params_cache, params_cache_lock, check_client_disconnected)
        await self._check_disconnect(check_client_disconnected, "After Stop Sequences Adjustment")

        # 调整Top P
        top_p_to_set = request_params.get('top_p', DEFAULT_TOP_P)
        await self._adjust_top_p(top_p_to_set, check_client_disconnected)
        await self._check_disconnect(check_client_disconnected, "End Parameter Adjustment")

        # 确保工具面板已展开，以便调整高级设置
        await self._ensure_tools_panel_expanded(check_client_disconnected)

        # 调整URL CONTEXT
        if ENABLE_URL_CONTEXT:
            await self._open_url_content(check_client_disconnected)
        else:
            self.logger.info(f"[{self.req_id}] URL Context 功能已禁用，跳过调整。")

        # 调整“思考预算”
        await self._handle_thinking_budget(request_params, check_client_disconnected)

        # 调整 Google Search 开关
        await self._adjust_google_search(request_params, check_client_disconnected)

    def _get_reasoning_effort_from_params(self, request_params: Dict[str, Any]) -> Optional[Any]:
        """
        从请求参数中智能提取思考模式的设置。
        优先检查 'extra_body.google.thinking_config'，如果找到有效设置则立即返回。
        否则，回退到顶层的 'reasoning_effort' 参数。
        """
        extra_body = request_params.get('extra_body')
        if isinstance(extra_body, dict):
            google_config = extra_body.get('google')
            if isinstance(google_config, dict):
                thinking_config = google_config.get('thinking_config')
                if isinstance(thinking_config, dict):
                    self.logger.info(f"[{self.req_id}] 检测到 'extra_body.google.thinking_config'。")
                    include_thoughts = thinking_config.get('include_thoughts')
                    thinking_budget = thinking_config.get('thinking_budget')

                    if include_thoughts is False:
                        self.logger.info(f"[{self.req_id}] 从 extra_body 解析: 关闭思考 (include_thoughts: false)。")
                        return 0

                    if isinstance(thinking_budget, int) and thinking_budget > 0:
                        self.logger.info(f"[{self.req_id}] 从 extra_body 解析: 使用预算 {thinking_budget}。")
                        return thinking_budget

                    if include_thoughts is True:
                        self.logger.info(f"[{self.req_id}] 从 extra_body 解析: 开启思考，无预算限制。")
                        return -1
        
        reasoning_effort = request_params.get('reasoning_effort')
        if reasoning_effort is not None:
             self.logger.info(f"[{self.req_id}] 未在 extra_body 中找到有效配置，回退到顶层 'reasoning_effort' 参数。")
        return reasoning_effort

    async def _handle_thinking_budget(self, request_params: Dict[str, Any], check_client_disconnected: Callable):
        """处理思考模式和预算的调整逻辑。"""
        
        reasoning_effort = self._get_reasoning_effort_from_params(request_params)
        directive = normalize_reasoning_effort(reasoning_effort)
        self.logger.info(f"[{self.req_id}] 思考模式指令: {format_directive_log(directive)}")

        thinking_toggle_success = await self._control_thinking_mode_toggle(
            should_be_enabled=directive.thinking_enabled,
            check_client_disconnected=check_client_disconnected
        )
        
        if not thinking_toggle_success:
            self.logger.warning(f"[{self.req_id}] 主思考开关未能设置为期望状态，将中止思考预算的后续调整。")
            return

        if not directive.thinking_enabled:
            self.logger.info(f"[{self.req_id}] 思考模式已关闭或已设置为关闭，无需调整预算。")
            return
            
        if not directive.budget_enabled:
            self.logger.info(f"[{self.req_id}] 关闭手动预算限制...")
            await self._control_thinking_budget_toggle(should_be_checked=False, check_client_disconnected=check_client_disconnected)
        else:
            self.logger.info(f"[{self.req_id}] 开启手动预算限制并设置预算值: {directive.budget_value} tokens")
            await self._control_thinking_budget_toggle(should_be_checked=True, check_client_disconnected=check_client_disconnected)
            await self._set_thinking_budget_value(directive.budget_value, check_client_disconnected)

    async def _set_thinking_budget_value(self, token_budget: int, check_client_disconnected: Callable):
        """设置思考预算的具体数值。"""
        self.logger.info(f"[{self.req_id}] 设置思考预算值: {token_budget} tokens")
        budget_input_locator = self.page.locator(THINKING_BUDGET_INPUT_SELECTOR)
        try:
            await expect_async(budget_input_locator).to_be_visible(timeout=5000)
            await self._check_disconnect(check_client_disconnected, "思考预算调整 - 输入框可见后")
            
            self.logger.info(f"[{self.req_id}] 设置思考预算为: {token_budget}")
            await budget_input_locator.fill(str(token_budget), timeout=5000)
            await self._check_disconnect(check_client_disconnected, "思考预算调整 - 填充输入框后")

            await asyncio.sleep(0.1)
            new_value_str = await budget_input_locator.input_value(timeout=3000)
            if int(new_value_str) == token_budget:
                self.logger.info(f"[{self.req_id}] ✅ 思考预算已成功更新为: {new_value_str}")
            else:
                self.logger.warning(f"[{self.req_id}] ⚠️ 思考预算更新后验证失败。页面显示: {new_value_str}, 期望: {token_budget}")
        except Exception as e:
            self.logger.error(f"[{self.req_id}] ❌ 调整思考预算时出错: {e}")
            if isinstance(e, ClientDisconnectedError): raise

    def _should_enable_google_search(self, request_params: Dict[str, Any]) -> bool:
        """根据请求参数或默认配置决定是否应启用 Google Search。"""
        if 'tools' in request_params and request_params.get('tools') is not None:
            tools = request_params.get('tools')
            has_google_search_tool = False
            if isinstance(tools, list):
                for tool in tools:
                    if isinstance(tool, dict):
                        if tool.get('google_search_retrieval') is not None:
                            has_google_search_tool = True
                            break
                        if tool.get('function', {}).get('name') == 'googleSearch':
                            has_google_search_tool = True
                            break
            self.logger.info(f"[{self.req_id}] 请求中包含 'tools' 参数。检测到 Google Search 工具: {has_google_search_tool}。")
            return has_google_search_tool
        else:
            self.logger.info(f"[{self.req_id}] 请求中不包含 'tools' 参数。使用默认配置 ENABLE_GOOGLE_SEARCH: {ENABLE_GOOGLE_SEARCH}。")
            return ENABLE_GOOGLE_SEARCH

    async def _adjust_google_search(self, request_params: Dict[str, Any], check_client_disconnected: Callable):
        """根据请求参数或默认配置，双向控制 Google Search 开关。"""
        self.logger.info(f"[{self.req_id}] 检查并调整 Google Search 开关...")
        should_enable_search = self._should_enable_google_search(request_params)
        toggle_selector = GROUNDING_WITH_GOOGLE_SEARCH_TOGGLE_SELECTOR
        try:
            toggle_locator = self.page.locator(toggle_selector)
            await expect_async(toggle_locator).to_be_visible(timeout=5000)
            await self._check_disconnect(check_client_disconnected, "Google Search 开关 - 元素可见后")

            is_checked_str = await toggle_locator.get_attribute("aria-checked")
            is_currently_checked = is_checked_str == "true"
            self.logger.info(f"[{self.req_id}] Google Search 开关当前状态: '{is_checked_str}'。期望状态: {should_enable_search}")

            if should_enable_search != is_currently_checked:
                action = "打开" if should_enable_search else "关闭"
                self.logger.info(f"[{self.req_id}] Google Search 开关状态与期望不符。正在点击以{action}...")
                await toggle_locator.click(timeout=CLICK_TIMEOUT_MS)
                await self._check_disconnect(check_client_disconnected, f"Google Search 开关 - 点击{action}后")
                await asyncio.sleep(0.5)
                new_state = await toggle_locator.get_attribute("aria-checked")
                if (new_state == "true") == should_enable_search:
                    self.logger.info(f"[{self.req_id}] ✅ Google Search 开关已成功{action}。")
                else:
                    self.logger.warning(f"[{self.req_id}] ⚠️ Google Search 开关{action}失败。当前状态: '{new_state}'")
            else:
                self.logger.info(f"[{self.req_id}] Google Search 开关已处于期望状态，无需操作。")
        except Exception as e:
            self.logger.error(f"[{self.req_id}] ❌ 操作 'Google Search toggle' 开关时发生错误: {e}")
            if isinstance(e, ClientDisconnectedError): raise

    async def _ensure_tools_panel_expanded(self, check_client_disconnected: Callable):
        """确保包含高级工具（URL上下文、思考预算等）的面板是展开的。"""
        self.logger.info(f"[{self.req_id}] 检查并确保工具面板已展开...")
        try:
            collapse_tools_locator = self.page.locator('button[aria-label="Expand or collapse tools"]')
            await expect_async(collapse_tools_locator).to_be_visible(timeout=5000)
            
            grandparent_locator = collapse_tools_locator.locator("xpath=../..")
            class_string = await grandparent_locator.get_attribute("class", timeout=3000)

            if class_string and "expanded" not in class_string.split():
                self.logger.info(f"[{self.req_id}] 工具面板未展开，正在点击以展开...")
                await collapse_tools_locator.click(timeout=CLICK_TIMEOUT_MS)
                await self._check_disconnect(check_client_disconnected, "展开工具面板后")
                await expect_async(grandparent_locator).to_have_class(re.compile(r'.*expanded.*'), timeout=5000)
                self.logger.info(f"[{self.req_id}] ✅ 工具面板已成功展开。")
            else:
                self.logger.info(f"[{self.req_id}] 工具面板已处于展开状态。")
        except Exception as e:
            self.logger.error(f"[{self.req_id}] ❌ 展开工具面板时发生错误: {e}")
            if isinstance(e, ClientDisconnectedError): raise

    async def _open_url_content(self,check_client_disconnected: Callable):
        """仅负责打开 URL Context 开关，前提是面板已展开。"""
        try:
            self.logger.info(f"[{self.req_id}] 检查并启用 URL Context 开关...")
            use_url_content_selector = self.page.locator(USE_URL_CONTEXT_SELECTOR)
            await expect_async(use_url_content_selector).to_be_visible(timeout=5000)
            
            is_checked = await use_url_content_selector.get_attribute("aria-checked")
            if "false" == is_checked:
                self.logger.info(f"[{self.req_id}] URL Context 开关未开启，正在点击以开启...")
                await use_url_content_selector.click(timeout=CLICK_TIMEOUT_MS)
                await self._check_disconnect(check_client_disconnected, "点击URLCONTEXT后")
                self.logger.info(f"[{self.req_id}] ✅ URL Context 开关已点击。")
            else:
                self.logger.info(f"[{self.req_id}] URL Context 开关已处于开启状态。")
        except Exception as e:
            self.logger.error(f"[{self.req_id}] ❌ 操作 USE_URL_CONTEXT_SELECTOR 时发生错误:{e}。")
            if isinstance(e, ClientDisconnectedError): raise

    async def _control_thinking_mode_toggle(self, should_be_enabled: bool, check_client_disconnected: Callable) -> bool:
        """控制主思考开关，并处理其被禁用的情况。"""
        toggle_selector = ENABLE_THINKING_MODE_TOGGLE_SELECTOR
        self.logger.info(f"[{self.req_id}] 控制主思考开关，期望状态: {'开启' if should_be_enabled else '关闭'}...")
        try:
            toggle_locator = self.page.locator(toggle_selector)
            await expect_async(toggle_locator).to_be_visible(timeout=5000)
            await self._check_disconnect(check_client_disconnected, "主思考开关 - 元素可见后")

            if await toggle_locator.is_disabled(timeout=1000):
                self.logger.info(f"[{self.req_id}] 主思考开关被禁用 (UI上为灰色)。")
                is_checked_str = await toggle_locator.get_attribute("aria-checked")
                current_state_is_enabled = is_checked_str == "true"
                if current_state_is_enabled == should_be_enabled:
                    self.logger.info(f"[{self.req_id}] ✅ 被禁用的主思考开关状态已符合预期，无需操作。")
                    return True
                else:
                    self.logger.error(f"[{self.req_id}] ❌ 无法设置思考模式：开关被禁用且其状态({current_state_is_enabled})与期望状态({should_be_enabled})冲突。")
                    return False

            is_checked_str = await toggle_locator.get_attribute("aria-checked")
            current_state_is_enabled = is_checked_str == "true"
            self.logger.info(f"[{self.req_id}] 主思考开关当前状态: {is_checked_str} (是否开启: {current_state_is_enabled})")

            if current_state_is_enabled != should_be_enabled:
                action = "开启" if should_be_enabled else "关闭"
                self.logger.info(f"[{self.req_id}] 主思考开关需要切换，正在点击以{action}思考模式...")
                await toggle_locator.click(timeout=CLICK_TIMEOUT_MS)
                await self._check_disconnect(check_client_disconnected, f"主思考开关 - 点击{action}后")
                await asyncio.sleep(0.5)
                new_state_str = await toggle_locator.get_attribute("aria-checked")
                new_state_is_enabled = new_state_str == "true"
                if new_state_is_enabled == should_be_enabled:
                    self.logger.info(f"[{self.req_id}] ✅ 主思考开关已成功{action}。新状态: {new_state_str}")
                    return True
                else:
                    self.logger.warning(f"[{self.req_id}] ⚠️ 主思考开关{action}后验证失败。期望: {should_be_enabled}, 实际: {new_state_str}")
                    return False
            else:
                self.logger.info(f"[{self.req_id}] 主思考开关已处于期望状态，无需操作。")
                return True
        except TimeoutError:
            self.logger.warning(f"[{self.req_id}] ⚠️ 主思考开关元素未找到或不可见（当前模型可能不支持思考模式）")
            return False
        except Exception as e:
            self.logger.error(f"[{self.req_id}] ❌ 操作主思考开关时发生错误: {e}")
            await save_error_snapshot(f"thinking_mode_toggle_error_{self.req_id}")
            if isinstance(e, ClientDisconnectedError): raise
            return False

    async def _control_thinking_budget_toggle(self, should_be_checked: bool, check_client_disconnected: Callable):
        """控制 "Thinking Budget" 滑块开关的状态。"""
        toggle_selector = SET_THINKING_BUDGET_TOGGLE_SELECTOR
        self.logger.info(f"[{self.req_id}] 控制 'Thinking Budget' 开关，期望状态: {'选中' if should_be_checked else '未选中'}...")
        try:
            toggle_locator = self.page.locator(toggle_selector)
            await expect_async(toggle_locator).to_be_visible(timeout=5000)
            await self._check_disconnect(check_client_disconnected, "思考预算开关 - 元素可见后")
            is_checked_str = await toggle_locator.get_attribute("aria-checked")
            current_state_is_checked = is_checked_str == "true"
            self.logger.info(f"[{self.req_id}] 思考预算开关当前 'aria-checked' 状态: {is_checked_str} (当前是否选中: {current_state_is_checked})")
            if current_state_is_checked != should_be_checked:
                action = "启用" if should_be_checked else "禁用"
                self.logger.info(f"[{self.req_id}] 思考预算开关当前状态与期望不符，正在点击以{action}...")
                await toggle_locator.click(timeout=CLICK_TIMEOUT_MS)
                await self._check_disconnect(check_client_disconnected, f"思考预算开关 - 点击{action}后")
                await asyncio.sleep(0.5)
                new_state_str = await toggle_locator.get_attribute("aria-checked")
                new_state_is_checked = new_state_str == "true"
                if new_state_is_checked == should_be_checked:
                    self.logger.info(f"[{self.req_id}] ✅ 'Thinking Budget' 开关已成功{action}。新状态: {new_state_str}")
                else:
                    self.logger.warning(f"[{self.req_id}] ⚠️ 'Thinking Budget' 开关{action}后验证失败。期望状态: '{should_be_checked}', 实际状态: '{new_state_str}'")
            else:
                self.logger.info(f"[{self.req_id}] 'Thinking Budget' 开关已处于期望状态，无需操作。")
        except Exception as e:
            self.logger.error(f"[{self.req_id}] ❌ 操作 'Thinking Budget toggle' 开关时发生错误: {e}")
            if isinstance(e, ClientDisconnectedError): raise

    async def _adjust_temperature(self, temperature: float, page_params_cache: dict, params_cache_lock: asyncio.Lock, check_client_disconnected: Callable):
        """调整温度参数。"""
        async with params_cache_lock:
            self.logger.info(f"[{self.req_id}] 检查并调整温度设置...")
            clamped_temp = max(0.0, min(2.0, temperature))
            if clamped_temp != temperature:
                self.logger.warning(f"[{self.req_id}] 请求的温度 {temperature} 超出范围 [0, 2]，已调整为 {clamped_temp}")
            cached_temp = page_params_cache.get("temperature")
            if cached_temp is not None and abs(cached_temp - clamped_temp) < 0.001:
                self.logger.info(f"[{self.req_id}] 温度 ({clamped_temp}) 与缓存值 ({cached_temp}) 一致。跳过页面交互。")
                return
            self.logger.info(f"[{self.req_id}] 请求温度 ({clamped_temp}) 与缓存值 ({cached_temp}) 不一致或缓存中无值。需要与页面交互。")
            temp_input_locator = self.page.locator(TEMPERATURE_INPUT_SELECTOR)
            try:
                await expect_async(temp_input_locator).to_be_visible(timeout=5000)
                await self._check_disconnect(check_client_disconnected, "温度调整 - 输入框可见后")
                current_temp_str = await temp_input_locator.input_value(timeout=3000)
                await self._check_disconnect(check_client_disconnected, "温度调整 - 读取输入框值后")
                current_temp_float = float(current_temp_str)
                self.logger.info(f"[{self.req_id}] 页面当前温度: {current_temp_float}, 请求调整后温度: {clamped_temp}")
                if abs(current_temp_float - clamped_temp) < 0.001:
                    self.logger.info(f"[{self.req_id}] 页面当前温度 ({current_temp_float}) 与请求温度 ({clamped_temp}) 一致。更新缓存并跳过写入。")
                    page_params_cache["temperature"] = current_temp_float
                else:
                    self.logger.info(f"[{self.req_id}] 页面温度 ({current_temp_float}) 与请求温度 ({clamped_temp}) 不同，正在更新...")
                    await temp_input_locator.fill(str(clamped_temp), timeout=5000)
                    await self._check_disconnect(check_client_disconnected, "温度调整 - 填充输入框后")
                    await asyncio.sleep(0.1)
                    new_temp_str = await temp_input_locator.input_value(timeout=3000)
                    new_temp_float = float(new_temp_str)
                    if abs(new_temp_float - clamped_temp) < 0.001:
                        self.logger.info(f"[{self.req_id}] ✅ 温度已成功更新为: {new_temp_float}。更新缓存。")
                        page_params_cache["temperature"] = new_temp_float
                    else:
                        self.logger.warning(f"[{self.req_id}] ⚠️ 温度更新后验证失败。页面显示: {new_temp_float}, 期望: {clamped_temp}。清除缓存中的温度。")
                        page_params_cache.pop("temperature", None)
                        await save_error_snapshot(f"temperature_verify_fail_{self.req_id}")
            except (ValueError, TypeError) as ve:
                self.logger.error(f"[{self.req_id}] 转换温度值为浮点数时出错. 错误: {ve}。清除缓存中的温度。")
                page_params_cache.pop("temperature", None)
                await save_error_snapshot(f"temperature_value_error_{self.req_id}")
            except Exception as pw_err:
                self.logger.error(f"[{self.req_id}] ❌ 操作温度输入框时发生错误: {pw_err}。清除缓存中的温度。")
                page_params_cache.pop("temperature", None)
                await save_error_snapshot(f"temperature_playwright_error_{self.req_id}")
                if isinstance(pw_err, ClientDisconnectedError): raise

    async def _adjust_max_tokens(self, max_tokens: int, page_params_cache: dict, params_cache_lock: asyncio.Lock, model_id_to_use: str, parsed_model_list: list, check_client_disconnected: Callable):
        """调整最大输出Token参数。"""
        async with params_cache_lock:
            self.logger.info(f"[{self.req_id}] 检查并调整最大输出 Token 设置...")
            min_val_for_tokens = 1
            max_val_for_tokens_from_model = 65536
            if model_id_to_use and parsed_model_list:
                current_model_data = next((m for m in parsed_model_list if m.get("id") == model_id_to_use), None)
                if current_model_data and current_model_data.get("supported_max_output_tokens") is not None:
                    try:
                        supported_tokens = int(current_model_data["supported_max_output_tokens"])
                        if supported_tokens > 0: max_val_for_tokens_from_model = supported_tokens
                        else: self.logger.warning(f"[{self.req_id}] 模型 {model_id_to_use} supported_max_output_tokens 无效: {supported_tokens}")
                    except (ValueError, TypeError): self.logger.warning(f"[{self.req_id}] 模型 {model_id_to_use} supported_max_output_tokens 解析失败")
            clamped_max_tokens = max(min_val_for_tokens, min(max_val_for_tokens_from_model, max_tokens))
            if clamped_max_tokens != max_tokens: self.logger.warning(f"[{self.req_id}] 请求的最大输出 Tokens {max_tokens} 超出模型范围，已调整为 {clamped_max_tokens}")
            cached_max_tokens = page_params_cache.get("max_output_tokens")
            if cached_max_tokens is not None and cached_max_tokens == clamped_max_tokens:
                self.logger.info(f"[{self.req_id}] 最大输出 Tokens ({clamped_max_tokens}) 与缓存值一致。跳过页面交互。")
                return
            max_tokens_input_locator = self.page.locator(MAX_OUTPUT_TOKENS_SELECTOR)
            try:
                await expect_async(max_tokens_input_locator).to_be_visible(timeout=5000)
                await self._check_disconnect(check_client_disconnected, "最大输出Token调整 - 输入框可见后")
                current_max_tokens_str = await max_tokens_input_locator.input_value(timeout=3000)
                current_max_tokens_int = int(current_max_tokens_str)
                if current_max_tokens_int == clamped_max_tokens:
                    self.logger.info(f"[{self.req_id}] 页面当前最大输出 Tokens ({current_max_tokens_int}) 与请求值 ({clamped_max_tokens}) 一致。更新缓存并跳过写入。")
                    page_params_cache["max_output_tokens"] = current_max_tokens_int
                else:
                    self.logger.info(f"[{self.req_id}] 页面最大输出 Tokens ({current_max_tokens_int}) 与请求值 ({clamped_max_tokens}) 不同，正在更新...")
                    await max_tokens_input_locator.fill(str(clamped_max_tokens), timeout=5000)
                    await self._check_disconnect(check_client_disconnected, "最大输出Token调整 - 填充输入框后")
                    await asyncio.sleep(0.1)
                    new_max_tokens_str = await max_tokens_input_locator.input_value(timeout=3000)
                    new_max_tokens_int = int(new_max_tokens_str)
                    if new_max_tokens_int == clamped_max_tokens:
                        self.logger.info(f"[{self.req_id}] ✅ 最大输出 Tokens 已成功更新为: {new_max_tokens_int}")
                        page_params_cache["max_output_tokens"] = new_max_tokens_int
                    else:
                        self.logger.warning(f"[{self.req_id}] ⚠️ 最大输出 Tokens 更新后验证失败。页面显示: {new_max_tokens_int}, 期望: {clamped_max_tokens}。清除缓存。")
                        page_params_cache.pop("max_output_tokens", None)
                        await save_error_snapshot(f"max_tokens_verify_fail_{self.req_id}")
            except (ValueError, TypeError) as ve:
                self.logger.error(f"[{self.req_id}] 转换最大输出 Tokens 值时出错: {ve}。清除缓存。")
                page_params_cache.pop("max_output_tokens", None)
                await save_error_snapshot(f"max_tokens_value_error_{self.req_id}")
            except Exception as e:
                self.logger.error(f"[{self.req_id}] ❌ 调整最大输出 Tokens 时出错: {e}。清除缓存。")
                page_params_cache.pop("max_output_tokens", None)
                await save_error_snapshot(f"max_tokens_error_{self.req_id}")
                if isinstance(e, ClientDisconnectedError): raise
    
    async def _adjust_stop_sequences(self, stop_sequences, page_params_cache: dict, params_cache_lock: asyncio.Lock, check_client_disconnected: Callable):
        """调整停止序列参数。"""
        async with params_cache_lock:
            self.logger.info(f"[{self.req_id}] 检查并设置停止序列...")
            normalized_requested_stops = set()
            if stop_sequences is not None:
                if isinstance(stop_sequences, str):
                    if stop_sequences.strip(): normalized_requested_stops.add(stop_sequences.strip())
                elif isinstance(stop_sequences, list):
                    for s in stop_sequences:
                        if isinstance(s, str) and s.strip(): normalized_requested_stops.add(s.strip())
            cached_stops_set = page_params_cache.get("stop_sequences")
            if cached_stops_set is not None and cached_stops_set == normalized_requested_stops:
                self.logger.info(f"[{self.req_id}] 请求的停止序列与缓存值一致。跳过页面交互。")
                return
            stop_input_locator = self.page.locator(STOP_SEQUENCE_INPUT_SELECTOR)
            remove_chip_buttons_locator = self.page.locator(MAT_CHIP_REMOVE_BUTTON_SELECTOR)
            try:
                initial_chip_count = await remove_chip_buttons_locator.count()
                removed_count = 0
                max_removals = initial_chip_count + 5
                while await remove_chip_buttons_locator.count() > 0 and removed_count < max_removals:
                    await self._check_disconnect(check_client_disconnected, "停止序列清除 - 循环开始")
                    try:
                        await remove_chip_buttons_locator.first.click(timeout=2000)
                        removed_count += 1
                        await asyncio.sleep(0.15)
                    except Exception: break
                if normalized_requested_stops:
                    await expect_async(stop_input_locator).to_be_visible(timeout=5000)
                    for seq in normalized_requested_stops:
                        await stop_input_locator.fill(seq, timeout=3000)
                        await stop_input_locator.press("Enter", timeout=3000)
                        await asyncio.sleep(0.2)
                page_params_cache["stop_sequences"] = normalized_requested_stops
                self.logger.info(f"[{self.req_id}] ✅ 停止序列已成功设置。缓存已更新。")
            except Exception as e:
                self.logger.error(f"[{self.req_id}] ❌ 设置停止序列时出错: {e}")
                page_params_cache.pop("stop_sequences", None)
                await save_error_snapshot(f"stop_sequence_error_{self.req_id}")
                if isinstance(e, ClientDisconnectedError): raise

    async def _adjust_top_p(self, top_p: float, check_client_disconnected: Callable):
        """调整Top P参数。"""
        self.logger.info(f"[{self.req_id}] 检查并调整 Top P 设置...")
        clamped_top_p = max(0.0, min(1.0, top_p))
        if abs(clamped_top_p - top_p) > 1e-9:
            self.logger.warning(f"[{self.req_id}] 请求的 Top P {top_p} 超出范围 [0, 1]，已调整为 {clamped_top_p}")
        top_p_input_locator = self.page.locator(TOP_P_INPUT_SELECTOR)
        try:
            await expect_async(top_p_input_locator).to_be_visible(timeout=5000)
            await self._check_disconnect(check_client_disconnected, "Top P 调整 - 输入框可见后")
            current_top_p_str = await top_p_input_locator.input_value(timeout=3000)
            current_top_p_float = float(current_top_p_str)
            if abs(current_top_p_float - clamped_top_p) > 1e-9:
                self.logger.info(f"[{self.req_id}] 页面 Top P ({current_top_p_float}) 与请求值 ({clamped_top_p}) 不同，正在更新...")
                await top_p_input_locator.fill(str(clamped_top_p), timeout=5000)
                await self._check_disconnect(check_client_disconnected, "Top P 调整 - 填充输入框后")
                await asyncio.sleep(0.1)
                new_top_p_str = await top_p_input_locator.input_value(timeout=3000)
                new_top_p_float = float(new_top_p_str)
                if abs(new_top_p_float - clamped_top_p) <= 1e-9:
                    self.logger.info(f"[{self.req_id}] ✅ Top P 已成功更新为: {new_top_p_float}")
                else:
                    self.logger.warning(f"[{self.req_id}] ⚠️ Top P 更新后验证失败。页面显示: {new_top_p_float}, 期望: {clamped_top_p}")
                    await save_error_snapshot(f"top_p_verify_fail_{self.req_id}")
            else:
                self.logger.info(f"[{self.req_id}] 页面 Top P ({current_top_p_float}) 与请求值 ({clamped_top_p}) 一致，无需更改")
        except (ValueError, TypeError) as ve:
            self.logger.error(f"[{self.req_id}] 转换 Top P 值时出错: {ve}")
            await save_error_snapshot(f"top_p_value_error_{self.req_id}")
        except Exception as e:
            self.logger.error(f"[{self.req_id}] ❌ 调整 Top P 时出错: {e}")
            await save_error_snapshot(f"top_p_error_{self.req_id}")
            if isinstance(e, ClientDisconnectedError): raise

    async def clear_chat_history(self, check_client_disconnected: Callable):
        """通过直接导航到新聊天URL来清空聊天记录，并确保临时聊天模式被重新启用。"""
        self.logger.info(f"[{self.req_id}] (v2) 开始清空聊天记录 (通过直接导航)...")
        await self._check_disconnect(check_client_disconnected, "Start Clear Chat")
        new_chat_url = f"https://{AI_STUDIO_URL_PATTERN}prompts/new_chat"
        try:
            self.logger.info(f"[{self.req_id}] (v2) 导航到: {new_chat_url}")
            await self.page.goto(new_chat_url, wait_until="domcontentloaded", timeout=30000)
            await expect_async(self.page.locator(PROMPT_TEXTAREA_SELECTOR)).to_be_visible(timeout=30000)
            self.logger.info(f"[{self.req_id}] (v2) ✅ 页面已成功导航并加载。")
            await self._enable_temporary_chat_mode_robustly(check_client_disconnected)
            await self._verify_chat_cleared(check_client_disconnected)
            self.logger.info(f"[{self.req_id}] (v2) ✅ 聊天历史清空完成。")
        except Exception as e_clear:
            self.logger.error(f"[{self.req_id}] (v2) 清空聊天过程中发生错误: {e_clear}")
            if not isinstance(e_clear, (ClientDisconnectedError, TimeoutError)):
                await save_error_snapshot(f"clear_chat_error_v2_{self.req_id}")
            raise

    async def _enable_temporary_chat_mode_robustly(self, check_client_disconnected: Callable):
        """健壮地检查并启用“临时聊天”模式。"""
        self.logger.info(f"[{self.req_id}] (v2) 检查并启用'临时聊天'模式...")
        await self._check_disconnect(check_client_disconnected, "Enable Temp Chat")
        try:
            toggle_locator = self.page.locator('button[aria-label="Temporary chat toggle"]')
            await expect_async(toggle_locator).to_be_visible(timeout=5000)
            button_classes = await toggle_locator.get_attribute("class") or ""
            if 'ms-button-active' in button_classes:
                self.logger.info(f"[{self.req_id}] (v2) '临时聊天'模式已激活，无需操作。")
            else:
                self.logger.info(f"[{self.req_id}] (v2) '临时聊天'模式未激活，正在点击...")
                await toggle_locator.click(timeout=CLICK_TIMEOUT_MS)
                await asyncio.sleep(0.5)
                updated_classes = await toggle_locator.get_attribute("class") or ""
                if 'ms-button-active' in updated_classes:
                    self.logger.info(f"[{self.req_id}] (v2) ✅ '临时聊天'模式已成功启用。")
                else:
                    self.logger.warning(f"[{self.req_id}] (v2) ⚠️ 点击后'临时聊天'模式状态验证失败。")
        except Exception as e:
            self.logger.warning(f"[{self.req_id}] (v2) ⚠️ 启用'临时聊天'模式时发生非致命错误: {e}")
            await save_error_snapshot(f"enable_temp_chat_error_{self.req_id}")

    async def _verify_chat_cleared(self, check_client_disconnected: Callable):
        """验证聊天已清空"""
        last_response_container = self.page.locator(RESPONSE_CONTAINER_SELECTOR).last
        await asyncio.sleep(0.5)
        await self._check_disconnect(check_client_disconnected, "After Clear Post-Delay")
        try:
            await expect_async(last_response_container).to_be_hidden(timeout=CLEAR_CHAT_VERIFY_TIMEOUT_MS - 500)
            self.logger.info(f"[{self.req_id}] ✅ 聊天已成功清空 (验证通过 - 最后响应容器隐藏)。")
        except Exception as verify_err:
            self.logger.warning(f"[{self.req_id}] ⚠️ 警告: 清空聊天验证失败 (最后响应容器未隐藏): {verify_err}")

    async def submit_prompt(self, prompt: str,image_list: List, check_client_disconnected: Callable):
        """提交提示到页面。"""
        self.logger.info(f"[{self.req_id}] 填充并提交提示 ({len(prompt)} chars)...")
        prompt_textarea_locator = self.page.locator(PROMPT_TEXTAREA_SELECTOR)
        autosize_wrapper_locator = self.page.locator('ms-prompt-input-wrapper ms-autosize-textarea')
        submit_button_locator = self.page.locator(SUBMIT_BUTTON_SELECTOR)
        try:
            await expect_async(prompt_textarea_locator).to_be_visible(timeout=5000)
            await self._check_disconnect(check_client_disconnected, "After Input Visible")
            await prompt_textarea_locator.evaluate(
                '''(element, text) => {
                    element.value = text;
                    element.dispatchEvent(new Event('input', { bubbles: true, cancelable: true }));
                    element.dispatchEvent(new Event('change', { bubbles: true, cancelable: true }));
                }''', prompt
            )
            await autosize_wrapper_locator.evaluate('(element, text) => { element.setAttribute("data-value", text); }', prompt)
            await self._check_disconnect(check_client_disconnected, "After Input Fill")
            if len(image_list) > 0:
                self.logger.info(f"[{self.req_id}] 待上传附件数量: {len(image_list)}")
                ok = await self._open_upload_menu_and_choose_file(image_list)
                if not ok: self.logger.error(f"[{self.req_id}] 上传文件时发生错误: 通过菜单方式未能设置文件")
            wait_timeout_ms_submit_enabled = 100000
            try:
                await self._check_disconnect(check_client_disconnected, "填充提示后等待发送按钮启用 - 前置检查")
                await expect_async(submit_button_locator).to_be_enabled(timeout=wait_timeout_ms_submit_enabled)
                self.logger.info(f"[{self.req_id}] ✅ 发送按钮已启用。")
            except Exception as e_pw_enabled:
                self.logger.error(f"[{self.req_id}] ❌ 等待发送按钮启用超时或错误: {e_pw_enabled}")
                await save_error_snapshot(f"submit_button_enable_timeout_{self.req_id}")
                raise
            await self._check_disconnect(check_client_disconnected, "After Submit Button Enabled")
            await asyncio.sleep(0.3)
            button_clicked = False
            try:
                self.logger.info(f"[{self.req_id}] 尝试点击提交按钮...")
                await self._handle_post_upload_dialog()
                await submit_button_locator.click(timeout=5000)
                self.logger.info(f"[{self.req_id}] ✅ 提交按钮点击完成。")
                button_clicked = True
            except Exception as click_err:
                self.logger.error(f"[{self.req_id}] ❌ 提交按钮点击失败: {click_err}")
                await save_error_snapshot(f"submit_button_click_fail_{self.req_id}")
            if not button_clicked:
                self.logger.info(f"[{self.req_id}] 按钮提交失败，尝试回车键提交...")
                submitted_successfully = await self._try_enter_submit(prompt_textarea_locator, check_client_disconnected)
                if not submitted_successfully:
                    self.logger.info(f"[{self.req_id}] 回车提交失败，尝试组合键提交...")
                    combo_ok = await self._try_combo_submit(prompt_textarea_locator, check_client_disconnected)
                    if not combo_ok:
                        self.logger.error(f"[{self.req_id}] ❌ 组合键提交也失败。")
                        raise Exception("Submit failed: Button, Enter, and Combo key all failed")
            await self._check_disconnect(check_client_disconnected, "After Submit")
        except Exception as e_input_submit:
            self.logger.error(f"[{self.req_id}] 输入和提交过程中发生错误: {e_input_submit}")
            if not isinstance(e_input_submit, ClientDisconnectedError): await save_error_snapshot(f"input_submit_error_{self.req_id}")
            raise
    
    async def _handle_post_upload_dialog(self):
        """处理上传后可能出现的授权/版权确认对话框。"""
        try:
            overlay_container = self.page.locator('div.cdk-overlay-container')
            if await overlay_container.count() == 0: return
            agree_texts = ['Agree', 'I agree', 'Allow', 'Continue', 'OK', '确定', '同意', '继续', '允许']
            for text in agree_texts:
                try:
                    btn = overlay_container.locator(f"button:has-text('{text}')")
                    if await btn.count() > 0 and await btn.first.is_visible(timeout=300):
                        await btn.first.click()
                        self.logger.info(f"[{self.req_id}] 上传后对话框: 点击按钮 '{text}'。")
                        await asyncio.sleep(0.3)
                        break
                except Exception: continue
            try:
                acknow_btn_locator = self.page.locator('button[aria-label*="copyright" i], button[aria-label*="acknowledge" i]')
                if await acknow_btn_locator.count() > 0 and await acknow_btn_locator.first.is_visible(timeout=300):
                    await acknow_btn_locator.first.click()
                    self.logger.info(f"[{self.req_id}] 上传后对话框: 点击版权确认按钮 (aria-label 匹配)。")
                    await asyncio.sleep(0.3)
            except Exception: pass
            try:
                overlay_backdrop = self.page.locator('div.cdk-overlay-backdrop.cdk-overlay-backdrop-showing')
                if await overlay_backdrop.count() > 0:
                    try:
                        await expect_async(overlay_backdrop).to_be_hidden(timeout=3000)
                        self.logger.info(f"[{self.req_id}] 上传后对话框遮罩层已隐藏。")
                    except Exception: self.logger.warning(f"[{self.req_id}] 上传后对话框遮罩层仍存在，后续提交可能被拦截。")
            except Exception: pass
        except Exception: pass

    async def _open_upload_menu_and_choose_file(self, files_list: List[str]) -> bool:
        """通过'Insert assets'菜单选择'上传/Upload'项并打开文件选择器设置文件。"""
        try:
            try:
                tb = self.page.locator('div.cdk-overlay-backdrop.cdk-overlay-transparent-backdrop.cdk-overlay-backdrop-showing')
                if await tb.count() > 0 and await tb.first.is_visible(timeout=300):
                    await self.page.keyboard.press('Escape')
                    await asyncio.sleep(0.2)
            except Exception: pass
            trigger = self.page.locator('button[aria-label="Insert assets such as images, videos, files, or audio"]')
            await trigger.click()
            menu_container = self.page.locator('div.cdk-overlay-container')
            try:
                await expect_async(menu_container.locator("div[role='menu']").first).to_be_visible(timeout=3000)
            except Exception:
                try:
                    await trigger.click()
                    await expect_async(menu_container.locator("div[role='menu']").first).to_be_visible(timeout=3000)
                except Exception:
                    self.logger.warning(f"[{self.req_id}] 未能显示上传菜单面板。")
                    return False
            try:
                upload_btn = menu_container.locator("div[role='menu'] button[role='menuitem'][aria-label='Upload File']")
                if await upload_btn.count() == 0: upload_btn = menu_container.locator("div[role='menu'] button[role='menuitem']:has-text('Upload File')")
                if await upload_btn.count() == 0:
                    self.logger.warning(f"[{self.req_id}] 未找到 'Upload File' 菜单项。")
                    return False
                btn = upload_btn.first
                await expect_async(btn).to_be_visible(timeout=2000)
                input_loc = btn.locator('input[type="file"]')
                if await input_loc.count() > 0:
                    await input_loc.set_input_files(files_list)
                    self.logger.info(f"[{self.req_id}] ✅ 通过菜单项(Upload File) 隐藏 input 设置文件成功: {len(files_list)} 个")
                else:
                    async with self.page.expect_file_chooser() as fc_info:
                        await btn.click()
                    file_chooser = await fc_info.value
                    await file_chooser.set_files(files_list)
                    self.logger.info(f"[{self.req_id}] ✅ 通过文件选择器设置文件成功: {len(files_list)} 个")
            except Exception as e_set:
                self.logger.error(f"[{self.req_id}] 设置文件失败: {e_set}")
                return False
            try:
                backdrop = self.page.locator('div.cdk-overlay-backdrop.cdk-overlay-backdrop-showing, div.cdk-overlay-backdrop.cdk-overlay-transparent-backdrop.cdk-overlay-backdrop-showing')
                if await backdrop.count() > 0:
                    await self.page.keyboard.press('Escape')
                    await asyncio.sleep(0.2)
            except Exception: pass
            await self._handle_post_upload_dialog()
            return True
        except Exception as e:
            self.logger.error(f"[{self.req_id}] 通过上传菜单设置文件失败: {e}")
            return False

    async def _try_enter_submit(self, prompt_textarea_locator, check_client_disconnected: Callable) -> bool:
        """优先使用回车键提交。"""
        import os
        try:
            host_os_from_launcher = os.environ.get('HOST_OS_FOR_SHORTCUT')
            is_mac_determined = False
            if host_os_from_launcher == "Darwin": is_mac_determined = True
            elif host_os_from_launcher in ["Windows", "Linux"]: is_mac_determined = False
            else:
                try: user_agent_data_platform = await self.page.evaluate("() => navigator.userAgentData?.platform || ''")
                except Exception:
                    user_agent_string = await self.page.evaluate("() => navigator.userAgent || ''")
                    user_agent_string_lower = user_agent_string.lower()
                    if "macintosh" in user_agent_string_lower or "mac os x" in user_agent_string_lower: user_agent_data_platform = "macOS"
                    else: user_agent_data_platform = "Other"
                is_mac_determined = "mac" in user_agent_data_platform.lower()

            await prompt_textarea_locator.focus(timeout=5000)
            await self._check_disconnect(check_client_disconnected, "After Input Focus")
            await asyncio.sleep(0.1)
            original_content = ""
            try: original_content = await prompt_textarea_locator.input_value(timeout=2000) or ""
            except Exception: pass
            self.logger.info(f"[{self.req_id}] 尝试回车键提交")
            try: await self.page.keyboard.press('Enter')
            except Exception:
                try: await prompt_textarea_locator.press('Enter')
                except Exception: pass
            await self._check_disconnect(check_client_disconnected, "After Enter Press")
            await asyncio.sleep(2.0)
            submission_success = False
            try:
                current_content = await prompt_textarea_locator.input_value(timeout=2000) or ""
                if original_content and not current_content.strip():
                    self.logger.info(f"[{self.req_id}] 验证方法1: 输入框已清空，回车键提交成功")
                    submission_success = True
                if not submission_success:
                    submit_button_locator = self.page.locator(SUBMIT_BUTTON_SELECTOR)
                    try:
                        is_disabled = await submit_button_locator.is_disabled(timeout=2000)
                        if is_disabled:
                            self.logger.info(f"[{self.req_id}] 验证方法2: 提交按钮已禁用，回车键提交成功")
                            submission_success = True
                    except Exception: pass
                if not submission_success:
                    try:
                        response_container = self.page.locator(RESPONSE_CONTAINER_SELECTOR)
                        if await response_container.count() > 0:
                            if await response_container.last.is_visible(timeout=1000):
                                self.logger.info(f"[{self.req_id}] 验证方法3: 检测到响应容器，回车键提交成功")
                                submission_success = True
                    except Exception: pass
            except Exception as verify_err:
                self.logger.warning(f"[{self.req_id}] 回车键提交验证过程出错: {verify_err}")
                submission_success = True
            if submission_success:
                self.logger.info(f"[{self.req_id}] ✅ 回车键提交成功")
                return True
            else:
                self.logger.warning(f"[{self.req_id}] ⚠️ 回车键提交验证失败")
                return False
        except Exception as shortcut_err:
            self.logger.warning(f"[{self.req_id}] 回车键提交失败: {shortcut_err}")
            return False

    async def _try_combo_submit(self, prompt_textarea_locator, check_client_disconnected: Callable) -> bool:
        """尝试使用组合键提交 (Meta/Control + Enter)。"""
        import os
        try:
            host_os_from_launcher = os.environ.get('HOST_OS_FOR_SHORTCUT')
            is_mac_determined = False
            if host_os_from_launcher == "Darwin": is_mac_determined = True
            elif host_os_from_launcher in ["Windows", "Linux"]: is_mac_determined = False
            else:
                try: user_agent_data_platform = await self.page.evaluate("() => navigator.userAgentData?.platform || ''")
                except Exception:
                    user_agent_string = await self.page.evaluate("() => navigator.userAgent || ''")
                    user_agent_string_lower = user_agent_string.lower()
                    if "macintosh" in user_agent_string_lower or "mac os x" in user_agent_string_lower: user_agent_data_platform = "macOS"
                    else: user_agent_data_platform = "Other"
                is_mac_determined = "mac" in user_agent_data_platform.lower()
            shortcut_modifier = "Meta" if is_mac_determined else "Control"
            shortcut_key = "Enter"
            await prompt_textarea_locator.focus(timeout=5000)
            await self._check_disconnect(check_client_disconnected, "After Input Focus")
            await asyncio.sleep(0.1)
            original_content = ""
            try: original_content = await prompt_textarea_locator.input_value(timeout=2000) or ""
            except Exception: pass
            self.logger.info(f"[{self.req_id}] 尝试组合键提交: {shortcut_modifier}+{shortcut_key}")
            try: await self.page.keyboard.press(f'{shortcut_modifier}+{shortcut_key}')
            except Exception:
                try:
                    await self.page.keyboard.down(shortcut_modifier)
                    await asyncio.sleep(0.05)
                    await self.page.keyboard.press(shortcut_key)
                    await asyncio.sleep(0.05)
                    await self.page.keyboard.up(shortcut_modifier)
                except Exception: pass
            await self._check_disconnect(check_client_disconnected, "After Combo Press")
            await asyncio.sleep(2.0)
            submission_success = False
            try:
                current_content = await prompt_textarea_locator.input_value(timeout=2000) or ""
                if original_content and not current_content.strip():
                    self.logger.info(f"[{self.req_id}] 验证方法1: 输入框已清空，组合键提交成功")
                    submission_success = True
                if not submission_success:
                    submit_button_locator = self.page.locator(SUBMIT_BUTTON_SELECTOR)
                    try:
                        is_disabled = await submit_button_locator.is_disabled(timeout=2000)
                        if is_disabled:
                            self.logger.info(f"[{self.req_id}] 验证方法2: 提交按钮已禁用，组合键提交成功")
                            submission_success = True
                    except Exception: pass
                if not submission_success:
                    try:
                        response_container = self.page.locator(RESPONSE_CONTAINER_SELECTOR)
                        if await response_container.count() > 0:
                            if await response_container.last.is_visible(timeout=1000):
                                self.logger.info(f"[{self.req_id}] 验证方法3: 检测到响应容器，组合键提交成功")
                                submission_success = True
                    except Exception: pass
            except Exception as verify_err:
                self.logger.warning(f"[{self.req_id}] 组合键提交验证过程出错: {verify_err}")
                submission_success = True
            if submission_success:
                self.logger.info(f"[{self.req_id}] ✅ 组合键提交成功")
                return True
            else:
                self.logger.warning(f"[{self.req_id}] ⚠️ 组合键提交验证失败")
                return False
        except Exception as combo_err:
            self.logger.warning(f"[{self.req_id}] 组合键提交失败: {combo_err}")
            return False

    async def get_response(self, check_client_disconnected: Callable) -> str:
        """获取响应内容。"""
        self.logger.info(f"[{self.req_id}] 等待并获取响应...")
        try:
            response_container_locator = self.page.locator(RESPONSE_CONTAINER_SELECTOR).last
            response_element_locator = response_container_locator.locator(RESPONSE_TEXT_SELECTOR)
            self.logger.info(f"[{self.req_id}] 等待响应元素附加到DOM...")
            await expect_async(response_element_locator).to_be_attached(timeout=90000)
            await self._check_disconnect(check_client_disconnected, "获取响应 - 响应元素已附加")
            submit_button_locator = self.page.locator(SUBMIT_BUTTON_SELECTOR)
            edit_button_locator = self.page.locator(EDIT_MESSAGE_BUTTON_SELECTOR)
            input_field_locator = self.page.locator(PROMPT_TEXTAREA_SELECTOR)
            self.logger.info(f"[{self.req_id}] 等待响应完成...")
            completion_detected = await _wait_for_response_completion(self.page, input_field_locator, submit_button_locator, edit_button_locator, self.req_id, check_client_disconnected, None)
            if not completion_detected: self.logger.warning(f"[{self.req_id}] 响应完成检测失败，尝试获取当前内容")
            else: self.logger.info(f"[{self.req_id}] ✅ 响应完成检测成功")
            final_content = await _get_final_response_content(self.page, self.req_id, check_client_disconnected)
            if not final_content or not final_content.strip():
                self.logger.warning(f"[{self.req_id}] ⚠️ 获取到的响应内容为空")
                await save_error_snapshot(f"empty_response_{self.req_id}")
                return ""
            self.logger.info(f"[{self.req_id}] ✅ 成功获取响应内容 ({len(final_content)} chars)")
            return final_content
        except Exception as e:
            self.logger.error(f"[{self.req_id}] ❌ 获取响应时出错: {e}")
            if not isinstance(e, ClientDisconnectedError): await save_error_snapshot(f"get_response_error_{self.req_id}")
            raise
