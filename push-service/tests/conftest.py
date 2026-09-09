"""Tests must never initialize the developer's on-disk database or send mail."""
import os

os.environ["DATABASE_URL"] = "sqlite://"
os.environ["DRY_RUN"] = "true"
os.environ["SMTP_HOST"] = ""
os.environ["ALERT_EMAIL_TO"] = ""
