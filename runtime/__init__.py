"""Рантайм мультипользовательского режима: пул сессий браузера."""
from .browser_session import BrowserSession
from .session_manager import SessionManager, event_to_msg

__all__ = ["BrowserSession", "SessionManager", "event_to_msg"]
