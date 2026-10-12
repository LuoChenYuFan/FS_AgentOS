"""Common error types."""


class FsError(Exception):
    """Base error for FS_AgentOS."""

    def __init__(self, message: str, *, code: str = "fs_error") -> None:
        super().__init__(message)
        self.code = code


class NotImplementedYet(FsError):
    """Placeholder for Phase 0+ features not wired yet."""

    def __init__(self, feature: str) -> None:
        super().__init__(f"not implemented yet: {feature}", code="not_implemented")
        self.feature = feature


class ModelQuotaExhausted(Exception):
    """The provider refused a model call because its free quota is gone."""

    def __init__(self, model: str) -> None:
        self.model = model.strip() or "当前"
        super().__init__(f"{self.model}模型额度已用光，暂时无法使用该功能")
