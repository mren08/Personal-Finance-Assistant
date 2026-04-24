from __future__ import annotations

from abc import ABC, abstractmethod


class PasswordResetMailer(ABC):
    @abstractmethod
    def send_password_reset_email(self, email: str, reset_url: str) -> None:
        raise NotImplementedError


class LoggingMailer(PasswordResetMailer):
    def __init__(self, logger) -> None:
        self._logger = logger

    def send_password_reset_email(self, email: str, reset_url: str) -> None:
        self._logger.info("Password reset link for %s: %s", email, reset_url)


def build_password_reset_mailer(app):
    return LoggingMailer(app.logger)
