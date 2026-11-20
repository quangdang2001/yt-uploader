"""Non-interactive Google credentials, shared by the VPS capture (studio_capture.py) and this PC's loop (loop/).

Never opens a browser (no InstalledAppFlow) and never writes the token file back: an expired token is refreshed in
memory only. A token that cannot be used without a person raises TokenError.
"""
from __future__ import annotations

from pathlib import Path


class TokenError(Exception):
    """Token Google khong dung duoc ma khong can nguoi. drive: Drive da ket noi (neu co)."""

    def __init__(self, message, drive=None):
        super().__init__(message)
        self.drive = drive


def load_creds(token: Path, scopes: list, label: str):
    """Doc token; het han thi refresh TRONG BO NHO.

    Khong bao gio ghi lai file token (uploader ghi no moi 5 phut) va khong bao gio
    mo InstalledAppFlow (khong ai ngoi truoc may de dang nhap). Token hong / bi thu hoi
    (RefreshError) -> TokenError. Loi mang khi refresh (TransportError) thi de nguyen:
    do la loi API tam thoi, khong phai token can dang nhap lai."""
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    token = Path(token)
    if not token.exists():
        raise TokenError(f"{label}: {token.name} missing")
    try:
        creds = Credentials.from_authorized_user_file(str(token), scopes)
    except Exception as e:
        raise TokenError(f"{label}: {token.name} unreadable ({type(e).__name__}: {e})") from e
    if creds.valid:
        return creds
    if not (creds.expired and creds.refresh_token):
        raise TokenError(f"{label}: token not valid and no refresh token")
    try:
        creds.refresh(Request())
    except RefreshError as e:
        raise TokenError(f"{label}: refresh failed ({type(e).__name__}: {e})") from e
    return creds
