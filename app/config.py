"""全局配置。

设计意图：所有密钥、模型名、路径、阈值只在这里定义一次，
其它模块一律 `from app.config import settings`，禁止直接读 os.environ。
好处：换模型/换路径只改一处；密钥不进代码；测试可通过环境变量覆盖。
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# jobpilot/ 目录（app/config.py → 上两级）
BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",  # .env 里多余变量不报错
    )

    # ---- 对话模型：DeepSeek（OpenAI 兼容协议）----
    deepseek_api_key: str
    deepseek_base_url: str = "https://api.deepseek.com"
    chat_model: str = "deepseek-chat"

    # ---- 向量模型：智谱 embedding-3 ----
    zhipu_api_key: str
    zhipu_base_url: str = "https://open.bigmodel.cn/api/paas/v4"
    embed_model: str = "embedding-3"

    # ---- 路径 ----
    base_dir: Path = BASE_DIR
    skills_dir: Path = BASE_DIR / "skills"
    data_dir: Path = BASE_DIR / "data"

    # ---- 上下文预算 ----
    # 发给模型的输入预算。取远低于模型上限的保守值，给输出和工具结果留空间。
    context_budget_tokens: int = 12000
    # 未摘要的最近消息条数下限：低于这个数就不触发摘要
    keep_recent_messages: int = 8
    # 迟滞系数：未摘要消息超过 keep_recent * 该系数才触发摘要。
    # 意义：避免每轮都摘一次（那样每轮会多一次 LLM 调用），
    # 触发时一次摘到位，使其在接下来若干轮内不再触发。
    summary_trigger_ratio: float = 2.0

    # ---- 模拟面试 ----
    # 一次完整模拟面试的题目数上限，到数自动进入复盘
    interview_max_questions: int = 5

    # ---- 计价（元 / 百万 token）----
    # 注意：这是示例价，务必按官方最新定价更新后再看成本数字
    price_input_per_million: float = 2.0
    price_output_per_million: float = 8.0

    # ---- 定时任务 ----
    # 自检脚本里不需要后台调度线程，用环境变量关掉即可
    scheduler_enabled: bool = True
    # 每日投递复盘的执行时间
    digest_hour: int = 9
    digest_minute: int = 0

    # ---- Redis（跨请求的会话锁与每日配额）----
    # 连不上会自动降级（不锁不限流），所以本地不装 Redis 也能跑
    redis_url: str = "redis://127.0.0.1:6379/0"
    # 单用户每日 token 上限，防止一个账号把模型的 API 额度刷完
    daily_token_quota: int = 300_000

    # ---- 数据库 ----
    # 本地默认 SQLite；部署时用环境变量覆盖为：
    #   mysql+pymysql://user:password@mysql:3306/jobpilot?charset=utf8mb4
    database_url: str = f"sqlite:///{(BASE_DIR / 'data' / 'jobpilot.db').as_posix()}"

    # ---- 认证 ----
    # 默认值仅用于本地开发；部署时必须在 .env 里换成随机长字符串
    jwt_secret: str = "dev-only-change-me-in-env"
    jwt_expire_minutes: int = 60 * 24 * 7  # 7 天


settings = Settings()
