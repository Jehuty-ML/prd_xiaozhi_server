import os
import asyncio
import yaml
from collections.abc import Mapping
from config.manage_api_client import (
    init_service,
    get_server_config,
    get_agent_models,
    get_correct_words,
    DeviceNotFoundException,
    DeviceBindException,
)


def get_project_dir():
    """获取项目根目录"""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + "/"


def read_config(config_path):
    with open(config_path, "r", encoding="utf-8") as file:
        config = yaml.safe_load(file)
    return config


async def load_config():
    """加载配置文件"""
    from core.utils.cache.manager import cache_manager, CacheType

    # 检查缓存
    cached_config = cache_manager.get(CacheType.CONFIG, "main_config")
    if cached_config is not None:
        return cached_config

    default_config_path = get_project_dir() + "config.yaml"
    custom_config_path = get_project_dir() + "data/.config.yaml"

    # 加载默认配置
    default_config = read_config(default_config_path)
    custom_config = read_config(custom_config_path)

    if custom_config.get("manager-api", {}).get("url"):
        config = await get_config_from_api_async(
            custom_config, default_local_server=default_config.get("server")
        )
    else:
        # 合并配置
        config = merge_configs(default_config, custom_config)
    # 初始化目录
    ensure_directories(config)

    # 缓存配置
    cache_manager.set(CacheType.CONFIG, "main_config", config)
    return config


async def get_config_from_api_async(config, default_local_server=None):
    """从Java API获取配置（异步版本）

    Args:
        config: 本地自定义配置（通常来自 data/.config.yaml，含 manager-api）
        default_local_server: 默认 config.yaml 中的 server 段（作兜底）

    合并规则：
    - 监听地址（ip/port/http_port/vision_explain/auth_key）始终以本地为准（进程绑定）
    - server.connection / server.metrics / server.resilience：
      默认 YAML < 智控台 API < data/.config.yaml 显式覆盖
    - server.auth.enabled：以 API 为准
    """
    # 初始化API客户端
    init_service(config)

    # 获取服务器配置
    config_data = await get_server_config()
    if config_data is None:
        raise Exception("Failed to fetch server config from API")

    config_data["read_config_from_api"] = True
    config_data["manager-api"] = {
        "url": config["manager-api"].get("url", ""),
        "secret": config["manager-api"].get("secret", ""),
    }

    api_server = config_data.get("server") or {}
    if not isinstance(api_server, dict):
        api_server = {}

    custom_server = config.get("server") or {}
    if not isinstance(custom_server, dict):
        custom_server = {}
    default_server = default_local_server or {}
    if not isinstance(default_server, dict):
        default_server = {}

    # 监听相关：本地优先（进程实际绑定地址）
    bind_server = {}
    bind_server.update(default_server)
    bind_server.update(custom_server)

    # auth：默认 < API < 本地；生产强制开启由 runtime_env.resolve_auth_enabled 处理
    merged_auth = {}
    if isinstance(default_server.get("auth"), dict):
        merged_auth.update(default_server["auth"])
    if isinstance(api_server.get("auth"), dict):
        merged_auth.update(api_server["auth"])
    if isinstance(custom_server.get("auth"), dict):
        merged_auth.update(custom_server["auth"])

    merged_server = {
        "ip": bind_server.get("ip", "0.0.0.0"),
        "port": bind_server.get("port", 8000),
        "http_port": bind_server.get("http_port", 8003),
        "vision_explain": bind_server.get("vision_explain", ""),
        "auth_key": bind_server.get("auth_key", ""),
        "auth": merged_auth,
    }

    # 保留 API 下发的网关类字段；本地显式配置可覆盖
    for key in (
        "environment",
        "websocket",
        "ota",
        "mcp_endpoint",
        "mqtt_gateway",
        "mqtt_signature_key",
        "udp_gateway",
        "mqtt_manager_api",
    ):
        if api_server.get(key) is not None:
            merged_server[key] = api_server.get(key)
        if key in custom_server:
            merged_server[key] = custom_server.get(key)

    # 本地/默认 environment 兜底
    if not merged_server.get("environment"):
        merged_server["environment"] = (
            custom_server.get("environment")
            or default_server.get("environment")
            or "development"
        )

    # connection：默认 < API < 本地 data/.config.yaml 显式覆盖
    merged_connection = {}
    if isinstance(default_server.get("connection"), dict):
        merged_connection.update(default_server["connection"])
    if isinstance(api_server.get("connection"), dict):
        merged_connection.update(api_server["connection"])
    if isinstance(custom_server.get("connection"), dict):
        merged_connection.update(custom_server["connection"])
    if merged_connection:
        merged_server["connection"] = merged_connection

    # metrics：同样支持 API 下发，本地可覆盖
    merged_metrics = {}
    if isinstance(default_server.get("metrics"), dict):
        merged_metrics.update(default_server["metrics"])
    if isinstance(api_server.get("metrics"), dict):
        merged_metrics.update(api_server["metrics"])
    if isinstance(custom_server.get("metrics"), dict):
        merged_metrics.update(custom_server["metrics"])
    if merged_metrics:
        merged_server["metrics"] = merged_metrics

    # resilience：默认 < API < 本地 data/.config.yaml 显式覆盖
    merged_resilience = {}
    if isinstance(default_server.get("resilience"), dict):
        merged_resilience.update(default_server["resilience"])
    if isinstance(api_server.get("resilience"), dict):
        merged_resilience.update(api_server["resilience"])
    if isinstance(custom_server.get("resilience"), dict):
        merged_resilience.update(custom_server["resilience"])
    if merged_resilience:
        merged_server["resilience"] = merged_resilience

    config_data["server"] = merged_server

    # 如果服务器没有prompt_template，则从本地配置读取
    if not config_data.get("prompt_template"):
        config_data["prompt_template"] = config.get("prompt_template")
    return config_data


async def reload_config_from_api():
    """热更新：重新读取本地 YAML + 拉取智控台配置。"""
    default_config_path = get_project_dir() + "config.yaml"
    custom_config_path = get_project_dir() + "data/.config.yaml"
    default_config = read_config(default_config_path)
    custom_config = read_config(custom_config_path)
    if not custom_config.get("manager-api", {}).get("url"):
        raise RuntimeError("当前未配置 manager-api，无法从智控台热更新")
    return await get_config_from_api_async(
        custom_config, default_local_server=default_config.get("server")
    )


async def get_private_config_from_api(config, device_id, client_id):
    """从Java API获取私有配置"""
    results = await asyncio.gather(
        get_agent_models(device_id, client_id, config["selected_module"]),
        get_correct_words(device_id),
        return_exceptions=True,
    )
    agent_result = results[0]
    correct_words = results[1] if not isinstance(results[1], Exception) else None

    # 抛出业务异常
    if isinstance(agent_result, DeviceNotFoundException):
        raise agent_result
    if isinstance(agent_result, DeviceBindException):
        raise agent_result

    private_config = agent_result if not isinstance(agent_result, Exception) else {}
    if correct_words:
        private_config["correct_words"] = correct_words
    return private_config


def ensure_directories(config):
    """确保所有配置路径存在"""
    dirs_to_create = set()
    project_dir = get_project_dir()  # 获取项目根目录
    # 日志文件目录
    log_dir = config.get("log", {}).get("log_dir", "tmp")
    dirs_to_create.add(os.path.join(project_dir, log_dir))

    # ASR/TTS模块输出目录
    for module in ["ASR", "TTS"]:
        if config.get(module) is None:
            continue
        for provider in config.get(module, {}).values():
            output_dir = provider.get("output_dir", "")
            if output_dir:
                dirs_to_create.add(output_dir)

    # 根据selected_module创建模型目录
    selected_modules = config.get("selected_module", {})
    for module_type in ["ASR", "LLM", "TTS"]:
        selected_provider = selected_modules.get(module_type)
        if not selected_provider:
            continue
        if config.get(module_type) is None:
            continue
        if config.get(selected_provider) is None:
            continue
        provider_config = config.get(module_type, {}).get(selected_provider, {})
        output_dir = provider_config.get("output_dir")
        if output_dir:
            full_model_dir = os.path.join(project_dir, output_dir)
            dirs_to_create.add(full_model_dir)

    # 统一创建目录（保留原data目录创建）
    for dir_path in dirs_to_create:
        try:
            os.makedirs(dir_path, exist_ok=True)
        except PermissionError:
            print(f"警告：无法创建目录 {dir_path}，请检查写入权限")


def merge_configs(default_config, custom_config):
    """
    递归合并配置，custom_config优先级更高

    Args:
        default_config: 默认配置
        custom_config: 用户自定义配置

    Returns:
        合并后的配置
    """
    if not isinstance(default_config, Mapping) or not isinstance(
        custom_config, Mapping
    ):
        return custom_config

    merged = dict(default_config)

    for key, value in custom_config.items():
        if (
            key in merged
            and isinstance(merged[key], Mapping)
            and isinstance(value, Mapping)
        ):
            merged[key] = merge_configs(merged[key], value)
        else:
            merged[key] = value

    return merged
