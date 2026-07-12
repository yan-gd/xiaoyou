from __future__ import annotations

import argparse
import getpass
import os
import sys

import qrcode

from .config import get_settings
from .database import Database
from .security import SecurityService


def validate_password(value: str) -> None:
    if len(value) < 12:
        raise ValueError("密码至少需要12个字符")
    if value.lower() == value or value.upper() == value:
        raise ValueError("密码需要同时包含大小写字母")
    if not any(char.isdigit() for char in value):
        raise ValueError("密码至少需要一个数字")


def create_admin(username: str) -> int:
    settings = get_settings()
    database = Database(settings.database_path)
    database.initialize()
    if database.admin_count() > 0:
        print("管理员已经存在。为避免远程抢注，初始化命令不会创建第二个管理员。", file=sys.stderr)
        return 2

    password = getpass.getpass("设置管理员密码：")
    confirm = getpass.getpass("再次输入密码：")
    if password != confirm:
        print("两次密码不一致。", file=sys.stderr)
        return 2
    try:
        validate_password(password)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    security = SecurityService(settings, database)
    secret, uri = security.generate_totp(username)
    recovery_codes = security.generate_recovery_codes()
    database.create_admin(
        username=username,
        password_hash=security.hash_password(password),
        totp_secret_encrypted=security.encrypt_totp_secret(secret),
        recovery_code_hashes=[security.secret_hash(code) for code in recovery_codes],
    )

    print("\n请使用TOTP验证器扫描下面的二维码：\n")
    qr = qrcode.QRCode(border=1)
    qr.add_data(uri)
    qr.make(fit=True)
    qr.print_ascii(invert=True)
    print(f"\n无法扫描时手动输入密钥：{secret}")
    print("\n一次性恢复码（只显示这一次，请离线保存）：")
    for code in recovery_codes:
        print(f"  {code}")
    print("\n管理员初始化完成。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="小悠·命轨观测台管理工具")
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create-admin", help="创建唯一管理员并绑定TOTP")
    create.add_argument("--username", default="yoyo", help="管理员用户名")
    args = parser.parse_args()
    if args.command == "create-admin":
        return create_admin(args.username.strip())
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
